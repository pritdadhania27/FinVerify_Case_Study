# The FastAPI service (spec §39).
#
# Not the research engine. A campaign runs from the host, where it can see the
# venv, the corpus and the run artifacts; this image serves the API over a
# database and a vector index that already exist. Building the whole embedding
# stack into a web image would add ~2GB of torch to a container that never
# embeds anything.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Only what the API imports. Deliberately NOT requirements.lock.txt: that file
# pins the full research environment including torch and camelot, none of which
# this service touches, and a web image that takes ten minutes to build is a
# web image nobody rebuilds.
RUN pip install --no-cache-dir \
    "fastapi==0.141.1" \
    "uvicorn[standard]==0.52.4" \
    "python-multipart==0.0.32" \
    "pydantic==2.13.4" \
    "sqlalchemy==2.0.44" \
    "psycopg[binary]==3.2.12" \
    "alembic==1.19.1" \
    "python-dotenv==1.2.1" \
    "qdrant-client==1.16.1"

COPY backend/ /app/backend/
COPY evaluation/ /app/evaluation/
COPY alembic.ini /app/alembic.ini

# Which commit this image was built from, echoed by GET /health. Every other
# field in that response reports on an EXTERNAL service - the database, the
# vector index - so all of them can be green while the process itself runs code
# from a fortnight ago. Passed at build time because the image has no git:
#   docker compose -f docker-compose.app.yml build \
#       --build-arg BUILD_REF=$(git rev-parse --short HEAD) api
ARG BUILD_REF=unknown
ENV FINVERIFY_BUILD_REF=${BUILD_REF}

# Non-root. The API accepts uploads, and an upload handler running as root is
# one path-traversal bug away from being a much worse problem.
#
# `/app/uploads` is created HERE, owned by that user, and not only in compose.
# Docker seeds a named volume from the image directory it is mounted over,
# including its ownership - so a mount point that does not exist in the image
# produces a root-owned volume, and the non-root process then cannot write to
# it. That is exactly how uploads failed with "Permission denied" after the
# read-only corpus mount was fixed: one deployment bug hiding behind another.
RUN useradd --create-home --uid 10001 finverify \
    && mkdir -p /app/documents/raw /app/uploads \
    && chown -R finverify:finverify /app
USER finverify

EXPOSE 8000

# Live QA stays OFF unless the operator sets FINVERIFY_ENABLE_LIVE_QA=1. A
# container that answered questions by default would spend the campaign's
# free-tier quota on whatever reached the port.
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
