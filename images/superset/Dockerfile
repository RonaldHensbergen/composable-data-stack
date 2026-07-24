FROM apache/superset:6.1.0@sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705

ARG IMAGE_PACKAGES=""

USER root

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY images/superset/requirements.txt /tmp/superset-requirements.txt
RUN pip install --no-cache-dir -r /tmp/superset-requirements.txt

RUN if [ -n "$IMAGE_PACKAGES" ]; then \
      pip install --no-cache-dir --target /app/.venv/lib/python3.10/site-packages $IMAGE_PACKAGES; \
    fi

COPY --chown=superset:superset --chmod=0444 images/superset/superset_config.py /app/pythonpath/superset_config.py
COPY --chown=superset:superset --chmod=0555 images/superset/entrypoint-web.sh /app/docker/entrypoint-web.sh
COPY --chown=superset:superset --chmod=0555 images/superset/init.sh /app/docker/init.sh

USER superset

ENTRYPOINT ["/app/docker/entrypoint-web.sh"]
