FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY corridor /app/corridor
COPY models/forest.json /app/models/forest.json
USER 65534:65534
ENTRYPOINT ["python", "-m", "corridor"]
