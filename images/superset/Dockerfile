FROM apache/superset:6.1.0

ARG IMAGE_PACKAGES=""

USER root
RUN if [ -n "$IMAGE_PACKAGES" ]; then \
      pip install --no-cache-dir --target /app/.venv/lib/python3.10/site-packages $IMAGE_PACKAGES; \
    fi

# Copy checked-in Superset config into the image.
COPY images/superset/superset_config.py /app/pythonpath/superset_config.py

# Copy and set up Superset scripts.
COPY images/superset/entrypoint.sh /app/docker/entrypoint-init.sh
COPY images/superset/init.sh /app/docker/init.sh
RUN chmod +x /app/docker/entrypoint-init.sh /app/docker/init.sh

USER superset

ENV SUPERSET_CONFIG_PATH=/app/pythonpath/superset_config.py

ENTRYPOINT ["/app/docker/entrypoint-init.sh"]
