FROM alpine:3.19

# Install Python and pip
RUN apk add --no-cache \
    python3 \
    py3-pip \
    && ln -sf python3 /usr/bin/python

ENV DAGSTER_HOME=/opt/dagster/dagster_home \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Create necessary directories
RUN mkdir -p $DAGSTER_HOME && \
    addgroup -g 1001 dagster && \
    adduser -D -u 1001 -G dagster dagster

WORKDIR /opt/dagster/app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir \
    dagster \
    dagster-postgres \
    dagster-docker

# Copy your Dagster definitions
COPY your_definitions.py .

# Set permissions
RUN chown -R dagster:dagster /opt/dagster

USER dagster
EXPOSE 4000

HEALTHCHECK --timeout=1s --start-period=3s --interval=3s --retries=20 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

ENTRYPOINT ["python", "-m", "dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "4000"]
