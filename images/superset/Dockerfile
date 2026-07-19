FROM apache/superset:6.1.0

ARG IMAGE_PACKAGES=""

USER root
RUN if [ -n "$IMAGE_PACKAGES" ]; then \
      pip install --no-cache-dir --target /app/.venv/lib/python3.10/site-packages $IMAGE_PACKAGES; \
    fi

# Copy and set up custom entrypoint script
COPY images/superset/entrypoint.sh /app/docker/entrypoint-init.sh
RUN chmod +x /app/docker/entrypoint-init.sh

USER superset

ENTRYPOINT ["/app/docker/entrypoint-init.sh"]
