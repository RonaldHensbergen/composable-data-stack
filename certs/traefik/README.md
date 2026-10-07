# Traefik TLS certificates

Traefik reads its `websecure` entrypoint certificate from this directory
(bind-mounted read-only by `modules/integration/traefik`). This repository
does not ship a certificate here: Traefik falls back to its own
auto-generated self-signed default certificate when the directory is
empty, which is sufficient for local development. Supply a real
certificate/key pair here (and reference it from
[`traefik/dynamic/keycloak.yml`](../../traefik/dynamic/keycloak.yml))
before exposing this stack beyond a trusted local network.
