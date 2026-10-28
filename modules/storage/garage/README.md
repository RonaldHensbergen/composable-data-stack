# Garage object storage

Garage provides a single-node S3-compatible endpoint for local and development
profiles. It creates the configured bucket and access key on first startup and
persists object data and metadata in separate named volumes.

## Configuration

| Field | Required | Description |
| --- | --- | --- |
| `bucket` | Yes | Initial bucket name (3-63 lowercase DNS-compatible characters) |
| `accessKeyFrom` | Yes | `secrets.<alias>` for the initial access key identifier (`GK` plus 32 hexadecimal characters) |
| `secretKeyFrom` | Yes | `secrets.<alias>` for the initial S3 secret key (64 hexadecimal characters) |
| `rpcSecretFrom` | Yes | `secrets.<alias>` for Garage's RPC secret (64 hexadecimal characters) |
| `port` | No | Host loopback port for S3 clients; defaults to `3900` |

The S3 region is fixed to `garage`.

Generate credentials without storing them in the profile:

```sh
openssl rand -hex 16 # access key suffix; prefix with GK
openssl rand -hex 32 # S3 secret key
openssl rand -hex 32 # Garage RPC secret
```

Declare the secret aliases under `spec.secrets.values` in the profile and mark
them `required: true` when used by a production-classified profile. The module
provides an `object-storage` contract with the endpoint, bucket, region, S3
credentials, addressing style, and TLS state for consumers.

Example module configuration:

```yaml
- id: object-storage
  source: ../../modules/storage/garage
  version: "0.1.0"
  config:
    bucket: cds-data
    accessKeyFrom: secrets.object_storage_access_key
    secretKeyFrom: secrets.object_storage_secret_key
    rpcSecretFrom: secrets.garage_rpc_secret
```

## Limitations

This module uses Garage's single-node mode with replication factor one. It has
no node or host-failure redundancy, and CDS marks it unsuitable for production.
The host API port is bound to `127.0.0.1`; other Compose services can use the
module service endpoint on the shared network. The admin API is bound to
container loopback and is not published.

Garage data is persisted in the `garage-data` and `garage-meta` named volumes.
Back them up and test restores independently; local persistence is not a
backup. Use a multi-node Garage deployment with an appropriate replication
layout for production workloads.

## Upstream documentation

- [Garage quick start](https://garagehq.deuxfleurs.fr/documentation/quick-start/)
- [Garage configuration reference](https://garagehq.deuxfleurs.fr/documentation/reference-manual/configuration/)
