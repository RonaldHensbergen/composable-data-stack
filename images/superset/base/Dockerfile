FROM apache/superset:6.1.0-py311@sha256:95b060f3450f3314019700642c20d2149dc0e0b41d65c0ef8a1bd8018fbf385c

USER root

# uv does the hash-verified install below, so install it from a hash lock too.
COPY images/superset/requirements-uv.txt /tmp/uv-requirements.txt
RUN /usr/local/bin/python -m pip install --no-cache-dir --upgrade \
      --require-hashes -r /tmp/uv-requirements.txt \
    && /usr/local/bin/uv --version \
    && /usr/local/bin/uvx --version

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# The lock deliberately raises some packages beyond ranges apache-superset
# declares (CVE fixes), so `uv pip check` would flag them by design.
COPY images/superset/requirements.txt /tmp/superset-requirements.txt
RUN uv pip install --python /app/.venv/bin/python --no-cache --upgrade \
      --no-build --require-hashes -r /tmp/superset-requirements.txt

# The application uses /app/.venv; remove unused system packaging tools and their
# vendored metadata so scanners do not report packages that cannot run.
RUN rm -rf \
      /usr/local/lib/python3.11/site-packages/pip \
      /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
      /usr/local/lib/python3.11/site-packages/setuptools \
      /usr/local/lib/python3.11/site-packages/setuptools-*.dist-info \
      /usr/local/lib/python3.11/site-packages/wheel \
      /usr/local/lib/python3.11/site-packages/wheel-*.dist-info \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11 /usr/local/bin/wheel

COPY --chown=superset:superset --chmod=0444 images/superset/superset_config.py /app/pythonpath/superset_config.py
COPY --chown=superset:superset --chmod=0444 images/superset/service-worker.js /app/superset/static/service-worker.js
COPY --chown=superset:superset --chmod=0555 images/superset/entrypoint-web.sh /app/docker/entrypoint-web.sh
COPY --chown=superset:superset --chmod=0555 images/superset/init.sh /app/docker/init.sh

USER superset

HEALTHCHECK --interval=10s --timeout=5s --start-period=180s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8088/health')"

ENTRYPOINT ["/app/docker/entrypoint-web.sh"]
