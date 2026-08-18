FROM apache/superset:6.1.0@sha256:08e3be59a16ef196aa6d65c8ac561ba53e5b463b972cd697d841adccc6d389bc

ARG IMAGE_PACKAGES=""
ARG UV_VERSION=0.11.26

USER root

RUN /usr/local/bin/python -m pip install --no-cache-dir --upgrade \
      "uv==${UV_VERSION}" \
    && /usr/local/bin/uv --version \
    && /usr/local/bin/uvx --version

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY images/superset/requirements.txt /tmp/superset-requirements.txt
RUN uv pip install --python /app/.venv/bin/python --no-cache --upgrade \
      -r /tmp/superset-requirements.txt

RUN if [ -n "$IMAGE_PACKAGES" ]; then \
      uv pip install --python /app/.venv/bin/python --no-cache $IMAGE_PACKAGES; \
    fi

# The application uses /app/.venv; remove unused system packaging tools and their
# vendored metadata so scanners do not report packages that cannot run.
RUN rm -rf \
      /usr/local/lib/python3.10/site-packages/pip \
      /usr/local/lib/python3.10/site-packages/pip-*.dist-info \
      /usr/local/lib/python3.10/site-packages/setuptools \
      /usr/local/lib/python3.10/site-packages/setuptools-*.dist-info \
      /usr/local/lib/python3.10/site-packages/wheel \
      /usr/local/lib/python3.10/site-packages/wheel-*.dist-info \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.10 /usr/local/bin/wheel

COPY --chown=superset:superset --chmod=0444 images/superset/superset_config.py /app/pythonpath/superset_config.py
COPY --chown=superset:superset --chmod=0555 images/superset/entrypoint-web.sh /app/docker/entrypoint-web.sh
COPY --chown=superset:superset --chmod=0555 images/superset/init.sh /app/docker/init.sh

USER superset

HEALTHCHECK --interval=10s --timeout=5s --start-period=180s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8088/health')"

ENTRYPOINT ["/app/docker/entrypoint-web.sh"]
