# Phase 3 — API FastAPI locale

## Périmètre livré

L'API est un projet Python installable indépendant dans **web/api**. Elle réutilise
`validate_training_spec()` de la phase 2. `run_training_spec()` reste disponible
pour les appelants Python ; aucun endpoint ne l'appelle. Il n'y a ni exécution
synchrone cachée, ni simulation de persistance, ni file BackgroundTasks.

Le contrat réservé de création renvoie 501. Les futurs services de jobs utiliseront
le moteur existant avec un contexte d'exécution choisi par le serveur. PostgreSQL,
authentification effective, utilisateurs, upload, ZenML, Zenflow et frontend restent
hors de cette livraison.

## Fichiers et responsabilités

| Fichiers créés sous web/api | Responsabilité |
|---|---|
| pyproject.toml, README.md | Package séparé, dépendances et lancement |
| src/signalflow_api/app.py | Fabrique FastAPI et assemblage |
| settings.py, __main__.py | Paramètres opérateur et commande locale |
| middleware.py | Corps borné, JSON strict, Host/Origin, identifiant et erreurs internes |
| errors.py | Traduction centralisée et expurgée des exceptions |
| dependencies.py | Identité et permissions remplaçables, mode local explicite |
| schemas/catalog.py, schemas/models.py | Projection des schémas canoniques et enveloppes Pydantic |
| services/training.py | Politique HTTP puis validation métier du moteur |
| routes/training.py | Cinq routes versionnées |
| __init__.py des packages | Organisation du package |
| examples/iris.json | Requête reproductible |
| tests/test_api.py | Contrats et frontières de sécurité |

Modifications complémentaires : pyproject.toml racine (découverte des tests et
chemin source API), README.md (accès au guide), docs/roadmap_finalisation.md.
Aucun module ML, configuration Docker ou volume n'est modifié par cette phase.
Les modifications préexistantes de ces fichiers restent préservées.

## Contrats HTTP

| Méthode / chemin | Entrée | Sortie |
|---|---|---|
| GET /api/v1/health | aucune | 200, status/service/mode/training_execution=false |
| GET /api/v1/training/schema | aucune | 200, JSON Schema draft 2020-12 |
| GET /api/v1/algorithms | aucune | 200, logistic_regression et schéma de C/max_iter |
| POST /api/v1/training/validate | spécification JSON | 200, valid=true et configuration |
| POST /api/v1/trainings | même spécification | 501 si valide ; 422 si invalide |

La spécification contient training, dataset, tracking ; monitoring est facultatif.
Les règles ML sont lues dans les ressources du package SignalFlow, sans recopier
les bornes des hyperparamètres. La projection HTTP retire CSV et directory et
interdit les exports actifs. Elle est vérifiée avant d'appeler le moteur :
aucune résolution de destination de monitoring fournie par le client n'est possible.
Les invariants entre champs (partitions, dimensions synthétiques) restent vérifiés
par le moteur et ne sont pas tous exprimables par le formulaire JSON Schema.

Les réponses d'erreur partagent cette structure :

```json
{
  "code": "TRAINING_VALIDATION_ERROR",
  "message": "La configuration HTTP est invalide.",
  "details": [{"path": "$.training.model.parameters.C", "rule": "exclusiveMinimum"}],
  "request_id": "identifiant-cree-par-le-serveur"
}
```

Aucune valeur rejetée, exception brute ou chemin interne n'est renvoyé. Les noms
de champs inconnus deviennent « * ». Les erreurs sémantiques sans emplacement
fourni par le moteur ont une liste de détails vide. request_id est aussi envoyé
dans X-Request-ID. Les erreurs inattendues sont journalisées avec cet identifiant,
sans texte d'exception susceptible de contenir une donnée sensible.

Codes : 400 JSON/Host invalides ; 401 Authorization non pris en charge ; 403 origine
ou permission refusée ; 408 délai de lecture ; 413 corps trop grand ; 415 format ou
compression refusés ; 422 contrat invalide ; 500 erreur interne ; 501 exécution
absente ; 503 authentification requise mais fournisseur absent.

## Exécution reproductible

Depuis la racine :

```bash
python -m pip install -e .
python -m pip install -e 'web/api[test]'
signalflow-api --port 8000
```

Puis dans un autre terminal :

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/training/schema
curl http://127.0.0.1:8000/api/v1/algorithms
curl -X POST http://127.0.0.1:8000/api/v1/training/validate \
  -H 'Content-Type: application/json' --data-binary @web/api/examples/iris.json
curl -X POST http://127.0.0.1:8000/api/v1/trainings \
  -H 'Content-Type: application/json' --data-binary @web/api/examples/iris.json
```

Swagger : http://127.0.0.1:8000/docs ; OpenAPI : /openapi.json.
La commande désactive la confiance dans les en-têtes de proxy et fixe l'écoute
à 127.0.0.1. Utiliser directement Uvicorn avec une autre adresse contournerait cette
garantie opérateur ; ne pas exposer cette application sur Internet.

## Sécurité et limites

- Corps limité à 65 536 octets réellement lus, même sans Content-Length ou avec
  une valeur mensongère ; lecture limitée à 5 secondes, profondeur JSON à 32.
- Refus des corps compressés, JSON ambigu à clés dupliquées, non UTF-8,
  nombres non finis, champs et algorithmes inconnus.
- Aucun chemin utilisateur lu, aucun dataset chargé, aucune écriture d'expérience.
- Host limité aux noms de boucle locale ; Origin absent ou exactement identique
  accepté. Aucun middleware CORS permissif ni confiance en X-User/X-Role.
- Mode local anonyme explicite : les permissions sont des points d'extension,
  **pas un RBAC authentifié opérationnel**. require_authentication ferme les routes
  métier ; health reste public. Aucun mot de passe/jeton n'est configuré.
- Les dépendances FastAPI restent dans web/api, pas dans le package ML.
- Pas encore de limitation de débit globale, de quotas utilisateur, TLS, sessions
  navigateur, authentification ni autorisations par projet. Le corps est borné,
  mais cela ne constitue pas une protection complète contre le déni de service.
- L'endpoint health ne vérifie ni Docker ni MLflow ni une base PostgreSQL.
- Aucune nouvelle validation Docker n'est revendiquée pour cette phase.

## Validation

Les tests ne requièrent ni Internet ni Docker. Les tests API utilisent TestClient
et des appels ASGI directs pour les corps fragmentés, délais et déconnexions.
Ils couvrent aussi la parité avec le moteur, la non-exécution, OpenAPI, le catalogue,
les restrictions de chemins avant appel du moteur et les extensions de permissions.

Les dépendances API sont facultatives pour les utilisateurs du moteur seul :
le module de tests web est ignoré si FastAPI ou httpx manque. Pour la validation
complète, installer l'extra test de web/api ; aucun de ces tests ne doit être ignoré.

Résultats exécutés le 16 novembre 2026, **hors réseau** (aucune requête vers un
index de paquets), avec Python 3.13.12 :

| Contrôle | Résultat |
| --- | --- |
| Construction des wheels `signalflow` et `signalflow-web-api` | Succès, sans isolation ni accès réseau |
| Schémas embarqués dans la wheel du moteur | 4 présents (`training`, `dataset`, `mlops`, `monitoring`) |
| Installation dans un virtualenv vierge hors dépôt | Succès, `--no-index` et wheels locales |
| `pip check` | `No broken requirements found.` |
| Tests API hors dépôt, contre les wheels installées | **55 réussis en 2,15 s** |
| Suite complète du dépôt | **335 réussis en 141,93 s**, aucun ignoré |
| Serveur Uvicorn live | health 200, algorithms 200, validate 200, trainings 501 |

Versions effectivement installées dans l'environnement isolé : `fastapi 0.162.7`,
`pydantic 2.14.3`, `uvicorn 0.54.0`, `starlette 1.10.0`, `httpx 0.28.1`,
`jsonschema 4.26.0`, `scikit-learn 1.9.1`, `mlflow-skinny 3.18.0`. Réponses live
observées :

```
GET  /api/v1/health               -> 200 {"status":"ok","training_execution":false}
GET  /api/v1/algorithms           -> 200 {"algorithms":[...],"execution_available":false}
POST /api/v1/training/validate    -> 200 {"valid":true,"configuration":{...}}
POST /api/v1/trainings            -> 501 TRAINING_EXECUTION_NOT_IMPLEMENTED
```

Les tests API sont **copiés hors du dépôt** avant exécution : ils importent
`signalflow` et `signalflow_api` depuis `site-packages`, donc les wheels réellement
installées, et non les sources locales. La suite complète, elle, s'exécute depuis le
dépôt contre les sources. Les 5 tests de la phase 2 auparavant ignorés faute de
scikit-learn/mlflow s'exécutent désormais.

Commandes de la preuve (virtualenv `/tmp/opencode/signalflow-phase3-venv`, journaux
et wheels dans `outputs/phase3-validation/`, ignoré par Git et régénérable par
`outputs/phase3-validation/verify.sh`) :

```bash
python3 -m pip wheel . --no-deps --no-build-isolation --no-index -w outputs/phase3-validation/dist
python3 -m pip wheel ./web/api --no-deps --no-build-isolation --no-index -w outputs/phase3-validation/dist
python3 -m venv /tmp/opencode/signalflow-phase3-venv
/tmp/opencode/signalflow-phase3-venv/bin/python -m pip install --no-index \
  --find-links outputs/stabilisation/wheelhouse \
  --find-links outputs/observability-validation/wheelhouse \
  --find-links /tmp/autobug-strix-wheels \
  'outputs/phase3-validation/dist/signalflow-1.0.0-py3-none-any.whl[ml,monitoring,observability]' \
  'outputs/phase3-validation/dist/signalflow_web_api-0.1.0-py3-none-any.whl[test]' 'pytest>=8,<10'
```

Limite : l'installation n'a pas été rejouée depuis une machine vierge sans cache ;
les dépendances proviennent de wheelhouses locales déjà présentes. Ce n'est donc pas
une preuve de disponibilité sur un registre public.

## Sources techniques

- [FastAPI — erreurs](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [FastAPI — TestClient](https://fastapi.tiangolo.com/tutorial/testing/)
- [Starlette — middleware ASGI](https://www.starlette.io/middleware/)

La protection du corps est un middleware ASGI afin de mesurer les octets réellement
reçus avant le parsing FastAPI. Les dépendances testées sont bornées dans le package.
