FROM apache/superset:6.1.0@sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705

ARG IMAGE_PACKAGES=""

USER root

# Upgrade OS packages to remediate HIGH/CRITICAL CVEs (openssl CVE-2026-45447,
# linux-libc-dev CVE-2026-63875, CVE-2026-64191, and others with upstream fixes).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Pin Python dependencies to versions that fix HIGH/CRITICAL CVEs:
#   Mako        CVE-2026-41205, CVE-2026-44307        → 1.3.12
#   PyJWT       CVE-2026-32597, CVE-2026-48526         → 2.13.0
#   cryptography GHSA-537c-gmf6-5ccf                   → 48.0.1
#   jaraco.context CVE-2026-23949                      → 6.1.0
#   msgpack     GHSA-6v7p-g79w-8964                    → 1.2.1
#   Pillow      CVE-2026-25990, -40192, -54058         → 12.3.0
#   pyOpenSSL   CVE-2026-27459                         → 26.0.0
#   pyarrow     CVE-2026-25087                         → 23.0.1
#   pyasn1      CVE-2026-30922, CVE-2026-59885         → 0.6.4
#   urllib3     CVE-2026-44431                         → 2.7.0
#   wheel       CVE-2026-24049                         → 0.46.2
RUN pip install --no-cache-dir \
    "Mako>=1.3.12" \
    "PyJWT>=2.13.0" \
    "cryptography>=48.0.1" \
    "jaraco.context>=6.1.0" \
    "msgpack>=1.2.1" \
    "Pillow>=12.3.0" \
    "pyOpenSSL>=26.0.0" \
    "pyarrow>=23.0.1" \
    "pyasn1>=0.6.4" \
    "urllib3>=2.7.0" \
    "wheel>=0.46.2"

RUN if [ -n "$IMAGE_PACKAGES" ]; then \
      pip install --no-cache-dir --target /app/.venv/lib/python3.10/site-packages $IMAGE_PACKAGES; \
    fi

COPY --chown=superset:superset --chmod=0444 images/superset/superset_config.py /app/pythonpath/superset_config.py
COPY --chown=superset:superset --chmod=0555 images/superset/entrypoint-web.sh /app/docker/entrypoint-web.sh
COPY --chown=superset:superset --chmod=0555 images/superset/init.sh /app/docker/init.sh

USER superset

ENTRYPOINT ["/app/docker/entrypoint-web.sh"]
