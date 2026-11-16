"""Mesurer off/local/full en processus neufs, mêmes données et ordre alterné.

Exécuter hors d'autres tâches lourdes. Résultat dans outputs/observability-overhead/.
Les temps comprennent les imports et le tracking ; aucun gain n'est déduit automatiquement.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import time

import yaml


def main() -> None:
    """Exécuter un échauffement puis N répétitions par mode et conserver les mesures brutes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--output-dir', type=Path, default=Path('outputs/observability-overhead'))
    args = parser.parse_args()
    if args.repetitions < 2:
        parser.error('au moins deux répétitions')
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for mode in ('off', 'local', 'full'):
        config = output/mode
        shutil.copytree(root/'examples/benchmark', config)
        path = config/'monitoring.yaml'
        settings = yaml.safe_load(path.read_text())
        settings['enabled'] = mode != 'off'
        settings['prometheus']['enabled'] = mode == 'full'
        settings['logging']['enabled'] = mode == 'full'
        path.write_text(yaml.safe_dump(settings))
    environment = dict(os.environ, PYTHONPATH=str(root/'src'), PYTHONHASHSEED='0',
                       OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                       SIGNALFLOW_OBSERVABILITY_DIR=str(output/'publications'))
    runs = []
    rng = random.Random(42)
    for repetition in range(args.repetitions+1):
        order = ['off','local','full']
        rng.shuffle(order)
        for mode in order:
            start = time.perf_counter()
            result = subprocess.run([sys.executable,'-m','signalflow','train','--config-dir',str(output/mode),
                                     '--output-dir',str(output/'runs')],cwd=output,env=environment,
                                    capture_output=True,text=True,timeout=180,check=True)
            wall = time.perf_counter()-start
            report=json.loads(result.stdout)
            runs.append({'mode':mode,'warmup':repetition==0,'round':repetition,'wall_seconds':wall,
                         'publication_io_seconds':report.get('observability',{}).get('publication_io_seconds',0),
                         'training_seconds':report['training_duration_seconds'],'ml_metrics':report['metrics'],
                         'artifacts':report['artifacts']})
            print(mode,repetition,round(wall,3),flush=True)
            (output/'measurements.json').write_text(json.dumps({'runs':runs},indent=2))
    summaries={}
    for mode in ('off','local','full'):
        values=[r['wall_seconds'] for r in runs if r['mode']==mode and not r['warmup']]
        summaries[mode]={'mean':statistics.mean(values),'stdev':statistics.stdev(values),
                         'median':statistics.median(values),'count':len(values)}
    assert all(r['ml_metrics']==runs[0]['ml_metrics'] for r in runs)
    result={'runs':runs,'summary':summaries,'same_ml_metrics':True,
            'method':'one warmup + repeated fresh processes per mode; same synthetic data; no automatic significance claim'}
    (output/'measurements.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(summaries,indent=2))


if __name__ == '__main__':
    main()
