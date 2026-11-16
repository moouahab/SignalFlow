FROM python:3.13.12-slim
RUN pip install --no-cache-dir prometheus-client==0.26.0
WORKDIR /app
COPY src/signalflow/__init__.py /app/signalflow/__init__.py
COPY src/signalflow/observability /app/signalflow/observability
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
EXPOSE 9108
CMD ["python", "-m", "signalflow.observability.exporter", "--host", "0.0.0.0", "--directory", "/signalflow", "--port", "9108"]
