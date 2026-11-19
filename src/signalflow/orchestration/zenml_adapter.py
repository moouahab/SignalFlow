"""Adaptateur local ZenML : le runner existant garde l’unique cycle MLflow."""
import json
from pathlib import Path
import uuid

import joblib

from signalflow.ml.adapters.sklearn import SklearnTrainer, TrainingResult
from signalflow.ml.errors import TrainingError
from .contracts import read_dataset, read_split, write_json


class ZenMLTrainer(SklearnTrainer):
    def __init__(self, output):
        self.output = Path(output)
        self.orchestration = None

    def train(self, training, dataset_config, dataset_path=None, *, instrumentation=None):
        from zenml.client import Client
        from .steps import classification
        # Les références de fichiers ci-dessous supposent un orchestrateur local.
        if Client().active_stack.orchestrator.flavor != 'local':
            raise TrainingError('ZenML nécessite un orchestrateur local')
        directory = self.output / 'orchestration' / uuid.uuid4().hex
        directory.mkdir(mode=0o700, parents=True)
        write_json(directory / 'request.json', {'training': training, 'dataset': dataset_config,
                   'path': str(dataset_path) if dataset_path else None})
        try:
            classification(directory=str(directory.resolve()))
            self.orchestration = json.loads((directory / 'execution.json').read_text())
            return TrainingResult(joblib.load(directory / 'model.joblib'),
                json.loads((directory / 'metrics.json').read_text()),
                json.loads((directory / 'duration.json').read_text()),
                read_dataset(directory), read_split(directory))
        except Exception:
            raise TrainingError('échec du pipeline ZenML ; consulter ses étapes privées') from None


def execution_references(directory):
    """Lire les seuls identifiants de run écrits par cette tentative.

    L'étape `load` consigne l'identifiant du pipeline dans `execution.json`.
    Une annulation avant cette écriture ne laisse aucune référence : il n'y a
    alors aucun pipeline ZenML à réconcilier.
    """
    if not directory:
        return []
    root = Path(directory) / 'output' / 'orchestration'
    references = []
    for path in sorted(root.glob('*/execution.json')):
        value = json.loads(path.read_text()).get('execution_id')
        if not value:
            continue
        try:
            uuid.UUID(str(value))
        except (TypeError, ValueError):
            raise RuntimeError('référence de pipeline ZenML invalide') from None
        references.append(str(value))
    return sorted(set(references))


def _step_status(ExecutionStatus, step, target):
    """Choisir un statut d'étape autorisé par les transitions ZenML 0.98.0."""
    if step.status == ExecutionStatus.CANCELLING:
        return ExecutionStatus.CANCELLED
    if step.status == ExecutionStatus.STOPPING:
        return ExecutionStatus.STOPPED
    return target


def synchronize(state, directory=None):
    """Réconcilier uniquement les runs de la tentative, puis vérifier le résultat.

    Une exception signale une réconciliation incomplète : l'appelant ne doit
    pas acquitter l'outbox et réessaiera lors d'un passage ultérieur.
    """
    from datetime import datetime, timezone
    from zenml.client import Client
    from zenml.enums import ExecutionStatus
    from zenml.models import PipelineRunUpdate, StepRunUpdate

    if state not in ('succeeded', 'cancelled', 'failed'):
        raise ValueError('état de synchronisation inconnu')
    references = execution_references(directory)
    if state == 'succeeded' and not references:
        raise RuntimeError('référence ZenML absente pour un entraînement réussi')
    if not references:
        return  # annulation avant création du pipeline : aucun run à modifier.

    client = Client()
    if state == 'succeeded':
        for reference in references:
            run = client.get_pipeline_run(uuid.UUID(reference))
            if run.status != ExecutionStatus.COMPLETED:
                raise RuntimeError(f'ZenML run incomplet : {run.status}')
        return

    step_status = ExecutionStatus.CANCELLED if state == 'cancelled' else ExecutionStatus.FAILED
    # Sur un pipeline statique, ZenML 0.98.0 recalcule le statut du run depuis
    # ses étapes et IGNORE un CANCELLED demandé. STOPPING est la seule demande
    # qui aboutit à STOPPED une fois les étapes non terminées closes.
    requested = ExecutionStatus.STOPPING if state == 'cancelled' else ExecutionStatus.FAILED
    expected = ({ExecutionStatus.STOPPED, ExecutionStatus.CANCELLED} if state == 'cancelled'
                else {ExecutionStatus.FAILED})

    for reference in references:
        run = client.get_pipeline_run(uuid.UUID(reference))
        if run.status.is_finished:
            # État terminal déjà atteint : ne jamais le réécrire.
            continue
        # STOPPING doit précéder la clôture des étapes : sans cela ZenML
        # requalifierait en RETRYING une étape encore réessayable (load).
        client.zen_store.update_run(run.id, PipelineRunUpdate(status=requested))
        run = client.get_pipeline_run(run.id)
        for step in run.steps.values():
            if step.status.is_finished:
                continue
            client.zen_store.update_run_step(step.id, StepRunUpdate(
                status=_step_status(ExecutionStatus, step, step_status),
                end_time=datetime.now(timezone.utc)))
        # Relecture obligatoire : update_run ne garantit pas le statut final.
        run = client.get_pipeline_run(run.id)
        if not run.status.is_finished or run.status not in expected:
            raise RuntimeError(f'réconciliation ZenML incomplète : {run.status}')
