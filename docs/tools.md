# Configuration et registre des outils SignalFlow

## Périmètre opérationnel

SignalFlow charge `project.yaml`, valide sa structure, puis charge et valide la
référence `config.tools`. Le résultat conserve le projet original et expose les
sélections d'outils. L'initialisation est une opération distincte, explicite.
Aucun pipeline d'entraînement ni service externe n'est lancé par le chargeur.

Le registre par défaut reconnaît pandas, scikit-learn, MLflow, Docker, Prometheus,
Loki et Trivy mais ne leur associe aucune factory. La configuration historique
`config/tools.yaml` les désactive tous et fonctionne sans SDK MLOps installé.
Le [pipeline de classification](pipeline.md) dispose maintenant d'adaptateurs
réels scikit-learn, MLflow et `metrics=local` (psutil), enregistrés explicitement
dans son propre registre. Le monitoring reste facultatif.
Les autres adaptateurs restent à développer.

## Contrat YAML

```yaml
schema_version: "0.0.1"
tools:
  training:
    provider: scikit-learn
    enabled: false
    # Facultatif ; le fichier doit exister si cette référence est déclarée.
    # config: ./training.yaml
```

- `schema_version` et `tools` sont obligatoires. La seule version acceptée est
  `0.0.1`. `tools: {}` est valide pour un projet sans outil.
- Les catégories sont facultatives : `data`, `training`, `experiment_tracking`,
  `execution`, `metrics`, `logging`, `security`.
- Chaque sélection requiert `provider` (chaîne non vide) et `enabled` (booléen).
  `config` est la seule propriété facultative. Toute autre propriété est refusée.
- `config` suit la convention du projet : `./...yaml`, relatif au **dossier de
  project.yaml**, même lorsque tools.yaml se trouve dans un sous-dossier.
- Le chargeur vérifie l'existence et le confinement de chaque `config`, même si
  l'outil est désactivé. Il ne lit pas ce fichier spécialisé et ne valide pas ses
  hyperparamètres : ce contrat appartiendra au futur module métier concerné.
- Aucun hyperparamètre, commande, nom de module Python ou secret n'a sa place ici.

Les différences avec la proposition initiale sont intentionnelles : chemins
préfixés par `./`, fournisseurs externes désactivés, et omission de la référence
`training.yaml` tant que ce fichier n'existe pas.

## Schéma et validation sémantique

`tools.schema.json` valide la version, les catégories, les types, les champs
obligatoires, les chemins déclaratifs et l'absence de propriétés inconnues.
Le registre vérifie ensuite le fournisseur autorisé **dans sa catégorie**, y
compris pour les outils désactivés. Ainsi, `mlflow` dans `data` est refusé.

Le catalogue des fournisseurs reste en Python, dans `KNOWN_PROVIDERS`, et n'est
pas dupliqué dans un `enum` JSON figé. Un nom supplémentaire devient autorisé
uniquement après `registry.register(...)` dans du code Python de confiance.
Valider tools.yaml avec le seul schéma JSON ne remplace donc pas la validation
complète du chargeur. Cette séparation est nécessaire pour que l'ajout d'un
fournisseur dans une catégorie existante ne nécessite aucun changement du schéma.
Une nouvelle catégorie implique en revanche une évolution du contrat JSON et du
catalogue ; un test garantit la concordance de leurs catégories.

Les erreurs de structure utilisent `ConfigValidationError`, avec chemins JSON et
rapport agrégé. Les erreurs YAML, de lecture et de chemins réutilisent les classes
existantes. Les erreurs du registre héritent toutes de `ConfigError` :

| Situation | Résultat |
| --- | --- |
| Nom ou paire catégorie/fournisseur inconnus | `ToolUnknownError` / `TOOL_UNKNOWN` |
| Fournisseur connu et désactivé | Validé, jamais initialisé |
| Fournisseur connu, activé, sans factory | Chargement valide ; `ToolUnavailableError` à l'initialisation |
| Factory enregistrée et outil activé | Factory appelée uniquement par `initialize()` |
| Import manquant dans une factory | `ToolUnavailableError` / `TOOL_UNAVAILABLE` |
| Autre erreur dans une factory | `ToolInitializationError` / `TOOL_INITIALIZATION` |

## Intégration au projet

La référence existante dans project.yaml est utilisée telle quelle :

```yaml
config:
  tools:
    path: ./tools.yaml
    description: Outils et integrations
    required: true
    schema: ./schemas/tools.schema.json
```

`enabled` omis signifie `true` pour cette référence. Si `enabled: false`, toute la
section est ignorée, sans lecture de tools.yaml ou de son schéma ; `required` n'est
alors pas appliqué. Sinon :

- `required: true` exige que tools.yaml existe ;
- `required: false` permet son absence, mais ne masque jamais un fichier présent
  invalide, un chemin dangereux ou un schéma absent lorsque tools.yaml existe.

Dans le chargeur générique, les autres références de project.yaml restent
déclaratives. `load_run_configuration()` charge ensuite training, dataset, mlops
et monitoring si activé ; le benchmark charge également sa propre configuration.

## API et architecture

```text
config/
  tools.yaml                       nouveau : sélections sans paramètres métier
  schemas/tools.schema.json        nouveau : contrat structurel
src/signalflow/
  config/loader/
    __init__.py                    modifié : orchestration et API compatible
    tools.py                       nouveau : chargement et validation des outils
    files.py                       modifié : lecture réellement bornée
    schema.py                      modifié : références JSON externes refusées
  tools/
    __init__.py                    nouveau : exports publics
    registry.py                    nouveau : catalogue, factories et erreurs
tests/config/test_tools.py          nouveau : tests du contrat et du registre
README.md                          modifié : usage et limites actuels
docs/tools.md                      nouveau : contrat et architecture
```

`config/project.yaml` référençait déjà tools.yaml : aucun changement supplémentaire
n'y était nécessaire. Les modifications locales préexistantes sont conservées.

- `load_config(config_dir, limits=...)` conserve sa signature et son dictionnaire
  de retour. Il valide désormais également les outils. Un ancien dossier de
  configuration déclarant tools.yaml obligatoire doit donc fournir ce fichier.
- `load_configuration(config_dir, limits=..., registry=...)` effectue ce même
  chargement et renvoie `LoadedConfiguration(project, tools)`. Il ne faut pas
  appeler les deux API successivement pour obtenir ces deux informations.
- `load_tools(...)` réutilise le parseur YAML strict, le validateur JSON Schema,
  les limites et `resolve_path()`. Il produit des `ToolDeclaration` immuables.
- `ToolRegistry.resolve(category, name)` retourne une `Provider` sans effet de
  bord. `factory is None` signifie connu sans implémentation ; une factory
  enregistrée peut encore échouer si ses dépendances manquent au moment de l'appel.
- `ToolRegistry.register(category, name, factory)` ajoute un fournisseur ou
  implémente une entrée connue. Remplacer une factory déjà enregistrée est refusé.
- `ToolRegistry.initialize(declarations)` vérifie d'abord toutes les sélections,
  puis appelle les factories actives dans l'ordre YAML. Le retour associe les
  catégories aux objets produits. Chaque appel crée de nouvelles instances.

```python
from signalflow.config.loader import load_configuration
from signalflow.tools import ToolRegistry

registry = ToolRegistry()
configuration = load_configuration("/chemin/vers/config", registry=registry)
instances = registry.initialize(configuration.tools)  # {} avec le fichier livré
```

Pour brancher un futur adaptateur, l'application enregistre une fonction Python
`registry.register("data", "mon-fournisseur", factory)` **avant** le chargement,
puis utilise le même registre pour l'initialisation. La factory reçoit la
`ToolDeclaration` de sa catégorie. Les imports de SDK doivent rester dans la
factory ; le registre ne construit jamais un import à partir du YAML.

## Sécurité et limites

Les références effectivement utilisées passent par le résolveur existant :
chemins absolus, sorties par `..` et liens symboliques externes refusés. Les
lectures sont bornées à la limite configurée plus un octet de détection. YAML
utilise le SafeLoader strict et refuse les doublons. Les `$ref` JSON externes
(réseau ou fichiers) sont refusés ; les références internes `#/$defs/...` restent
prises en charge.

Le chargeur et le registre n'émettent aucun log de configuration. Les messages
d'erreurs des factories sont remplacés par un diagnostic typé sans leur contenu
ni leur traceback chaîné, pour éviter de divulguer des identifiants de SDK. Les
adaptateurs restent du code de confiance : leurs propres logs et effets de bord
relèvent de leur implémentation. Ne pas stocker de secrets dans tools.yaml.

Aucun cache global, aucune installation automatique de dépendances, aucune
connexion à un service n'est ajoutée. Le registre ne garantit pas de rollback si
une factory échoue après l'initialisation d'une autre ; les contrats de fermeture
et le cycle de vie des futures intégrations restent à définir. Les vérifications
de chemins existantes ne constituent pas une isolation contre des modifications
concurrentes du système de fichiers par un autre processus.

## Vérification de la mission configuration, avant ajout du pipeline

- Suite initiale : **71 tests réussis**.
- Suite complète après intégration : **123 tests réussis**.
- Wheel construite hors réseau avec `pip wheel . --no-deps --no-build-isolation
  --no-index` et les outils setuptools/wheel du Python système.
- Installation de la wheel dans un répertoire temporaire, sans réinstaller les
  dépendances déjà présentes dans l'environnement de test.
- Import vérifié depuis cette installation, exécution hors dépôt avec copie de
  la configuration, chemin explicite, `./config` et `SIGNALFLOW_CONFIG`.
- Aucun import de pandas, sklearn, MLflow, Docker ou prometheus_client pendant ce
  contrôle. Aucun YAML utilisateur embarqué dans la wheel.

Le contrôle d'installation utilise les dépendances Python existantes ; il ne
constitue pas un test de téléchargement sur une machine vierge. Les tests des
adaptateurs réels livrés ensuite sont décrits dans [la documentation du pipeline](pipeline.md).

## Couche d’observabilité

Le pipeline réutilise `LocalMonitor` via `LoggedInstrumentation`. `TelemetrySession`
publie des agrégats locaux et des événements structurés ; l’exporteur persistant et
Alloy assurent les échanges réseau hors du processus ML. Le registre générique ne
change pas et les interfaces MLflow restent conservées. Voir les contrats de
configuration, métriques et reprise dans [le guide de la stack](observability_stack.md).
Les erreurs de publication sont diagnostiquées sans annuler un entraînement valide.
