# SPDX-License-Identifier: AGPL-3.0-or-later
FROM docker.io/library/python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/data/cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 investbell \
    && useradd --uid 10001 --gid 10001 --home-dir /data --no-create-home investbell \
    && mkdir /data \
    && chown 10001:10001 /data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY investbell/ investbell/
COPY static/ static/
COPY scripts/ scripts/
COPY tests/ tests/
COPY deploy/ deploy/
COPY docs/ docs/
COPY LICENSE README.md .gitignore .containerignore Containerfile ./

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8765
# OCI images do not carry HEALTHCHECK; deploy/investbell-dashboard.container defines it.
CMD ["python", "-m", "investbell.server", "--host", "0.0.0.0", "--port", "8765", "--data-dir", "/data"]
