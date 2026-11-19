FROM python:3.12-slim AS local
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY web/api ./web/api
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir '.[ml,monitoring,postgres]' ./web/api \
    && useradd --uid 10001 --create-home signalflow \
    && mkdir -p /data/datasets /data/jobs /data/registry \
    && chown -R signalflow:signalflow /data
USER signalflow
CMD ["signalflow-api"]

# Target optionnel : deux environnements pour les contraintes Pydantic distinctes.
FROM python:3.14-slim AS zenml
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY web/api ./web/api
COPY alembic.ini ./
COPY migrations ./migrations
COPY constraints/zenml-py314.txt ./constraints/zenml-py314.txt
RUN pip install --no-cache-dir '.[ml,monitoring,postgres]' ./web/api \
    && python -m venv /opt/zenml \
    && /opt/zenml/bin/pip install --no-cache-dir -c constraints/zenml-py314.txt '.[ml,monitoring,zenml]' \
    && pip check && /opt/zenml/bin/pip check \
    && useradd --uid 10001 --create-home signalflow \
    && mkdir -p /data/datasets /data/jobs /data/check /data/registry \
    && chown -R signalflow:signalflow /data
USER signalflow
ENV SIGNALFLOW_ORCHESTRATOR=zenml SIGNALFLOW_ZENML_PYTHON=/opt/zenml/bin/python
CMD ["signalflow-worker"]
