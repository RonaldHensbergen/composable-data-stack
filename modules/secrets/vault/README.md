# Vault secrets provider

Secret provider module that can retrieve secrets from HashiCorp Vault.

## Purpose

Provides a local Vault dev container for profiles that need a secrets
backend or want applications to read secrets directly from Vault.

## Known limitations

- Runs Vault in dev mode, state is ephemeral (tmpfs) and nothing survives a container restart
- Declares `productionSuitable: false`, do not use in production
- No healthcheck is defined

## Upstream documentation

- [HashiCorp Vault documentation](https://developer.hashicorp.com/vault/docs)

## Configuration notes

- The dev root token comes from `tokenFrom` and is injected as `VAULT_DEV_ROOT_TOKEN_ID`
- The host port is bound to `127.0.0.1:8200` only, other services reach Vault via the `address` config, for example `http://vault:8200` inside the stack network
- `mountPath` defaults to `secret/data`
