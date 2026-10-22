FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt

COPY environment/requirements.lock /tmp/requirements.lock
# nodejs, npm and procps are what Harbor's Claude Code installer would apt-get at run time, where the builder has
# no route to the Debian mirrors; having them here makes that step a no-op.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq ripgrep nodejs npm procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 harbor \
    && pip install --no-cache-dir -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock \
    && python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/opt/redwood-models')" \
    && chmod -R a=rX /opt/redwood-models

COPY environment/redwood_kit/ /opt/redwood_kit/
COPY environment/advance.py /usr/local/libexec/redwood-advance
COPY environment/entrypoint.sh /usr/local/bin/redwood-entrypoint
# Starting workspace: the verifier's baseline for what the builder changed in build_r0.
COPY environment/app/workspace/ /opt/redwood-baseline/workspace/
# Everything R1-only lives in this root-only overlay until advance.py reveals it.
COPY environment/corpus/R1/ /opt/redwood-overlays/R1/R/
COPY environment/r1/ /opt/redwood-overlays/R1/
COPY data/db_r1.json /opt/redwood-overlays/R1/db_r1.json

WORKDIR /app
COPY environment/app/ /app/
COPY environment/corpus/R0/ /app/R/
COPY data/db.json /opt/redwood-data/db.json

# /app/R is readable by the builder (group harbor) and not by the user the agent runs as.
# The host console (schema, validation, store) and its practice seed are root-only: the builder
# reaches the console through billing/client.py and the published schema.json.
RUN python -c 'import json; from pathlib import Path; from redwood_kit.billing.store import Billing; Path("/app/framework/client_api/schema.json").write_text(json.dumps(Billing(release="R0").describe(), indent=2) + "\n")' \
    && mkdir -p /tests /logs/verifier /logs/agent/pi/sessions \
    && echo R0 > /etc/redwood-release && chmod 444 /etc/redwood-release \
    && chmod -R a=rX /opt/redwood_kit \
    && find /opt/redwood_kit -name __pycache__ -prune -exec rm -rf {} + \
    && cd /opt/redwood_kit/billing \
    && chmod 600 schema.py validation.py store.py development_seed.json \
    && cd /app \
    && chmod 700 /usr/local/libexec/redwood-advance \
    && chmod 755 /usr/local/bin/redwood-entrypoint \
    && chmod -R go-rwx /opt/redwood-baseline /opt/redwood-overlays /opt/redwood-data /tests /logs/verifier \
    && chmod 711 /logs \
    && chmod -R a=rX /app \
    && chown -R root:harbor /app/R && chmod -R u=rX,g=rX,o= /app/R \
    && chown -R harbor:harbor /logs/agent /app/workspace \
    && chmod -R go-rwx /logs/agent \
    && chmod -R u+rwX /app/workspace

ENTRYPOINT ["/usr/local/bin/redwood-entrypoint"]
CMD ["sleep", "infinity"]
