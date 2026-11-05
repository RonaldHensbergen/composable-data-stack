# PostgreSQL via CloudNativePG (experimental)

Kubernetes-native, operator-managed PostgreSQL for the `--target helm`
render target. It provides the same `sql-database` contract as
[`modules/warehouse/postgres`](../../../modules/warehouse/postgres), so
consumers (Dagster, Superset, Keycloak, dbt, ...) bind to it with no
consumer-side changes — only the profile's warehouse module `source` differs
(issue #768, docs/haven-parity-plan.md workstream 3.6).

The module is **experimental** (`modules-experimental/`,
`productionSuitable: false`) and **Helm-only**: it has no compose
implementation, so rendering a profile containing it with the default
`--target compose` fails closed (E070/E072).

## What gets rendered

`cds render <profile> --target helm` emits two extra chart templates for the
module (it runs no pods of its own, so no ServiceAccount/NetworkPolicy are
rendered for it):

- `templates/<id>-manifest-cluster.yaml` — a CloudNativePG
  [`Cluster`](https://cloudnative-pg.io/documentation/current/cloudnative-pg.v1/#postgresql-cnpg-io-v1-Cluster)
  CR (`postgresql.cnpg.io/v1`) named `{{ .Release.Name }}-<id>`, with
  `instances`, `imageName`, `storage.size`, an `initdb` bootstrap of the
  configured database/owner, `postgresql.pg_hba` from
  `config.authentication.hostAuth`, and `inheritedMetadata.labels` that
  propagate the CDS module labels onto the operator-created pods (so the
  binding-derived NetworkPolicy edges of consumers select the database pods).
- `templates/<id>-manifest-app-credentials.yaml` — a Secret named
  `{{ .Release.Name }}-<id>-app` with `username`/`password` keys, which the
  Cluster's `bootstrap.initdb.secret` reads. The password is a
  `${secrets.*}` reference: the renderer rewrites it to a Helm `required`
  lookup of `.Values.secrets`, so the value is supplied at install time
  (`helm install --set secrets.CDS_...=...` / values file) and never stored
  in the committed chart.

## The advertised endpoint

CloudNativePG creates `<cluster>-rw` (read-write primary), `<cluster>-ro`,
and `<cluster>-r` Services for every Cluster. The `sql-database` contract
advertises `host: <id>-rw`, which the renderer release-scopes to
`{{ .Release.Name }}-<id>-rw`. The module declares that Service under
`implementation.kubernetes.services` (declared, not rendered), so
contract-host validation (E084/E150), consumer binding host release-scoping,
and `waitFor` gates all treat it like a rendered workload Service.

## Prerequisites at runtime

CDS renders the `Cluster` CR but does not install the CloudNativePG
operator — the module declares it under `implementation.kubernetes.operators`,
and CDS surfaces that prerequisite at every step:

- `cds render` / `cds test` / `cds up --target helm` warn with **W130** naming
  the operator, its CRD, and the install command; the rendered chart's
  `templates/NOTES.txt` repeats the same note after `helm install`.
- `cds up --target helm` additionally checks the cluster before installing
  (`kubectl get crd clusters.postgresql.cnpg.io`) and fails closed with
  **E155** while the CRD is absent, instead of letting `helm` fail with
  "no matches for kind \"Cluster\"".

Install the operator on the target cluster out of band (official chart,
installs controller + webhooks + RBAC + CRDs):

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm repo update
helm upgrade --install cnpg \
  --namespace cnpg-system \
  --create-namespace \
  cnpg/cloudnative-pg
```

Verify, then deploy the CDS chart (`cds up --target helm`, or
`helm upgrade --install` of the rendered chart):

```bash
kubectl get crd clusters.postgresql.cnpg.io                       # CRD registered
kubectl -n cnpg-system rollout status deployment/cnpg-cloudnative-pg  # operator ready
```

cert-manager is not required for a basic install — the operator manages its
own webhook and cluster CA by default; install cert-manager only if you want
your database serving certificates issued by cert-manager instead.

## Choosing this module vs. the Compose `postgres` module

- Use `modules/warehouse/postgres` for local/dev Compose stacks (it is the
  stable, Compose-first module, with the built-in pgBackRest `backup-target`
  sidecar).
- Use `modules-experimental/warehouse/postgres-operator` when the profile
  targets Kubernetes only and you want HA/failover managed by an operator
  (`config.instances: 3` for production) instead of a single StatefulSet+PVC.

See the "Choosing a warehouse module" section of
[docs/modules.md](../../../docs/modules.md) and the demo profile
[`profiles/local-kubernetes-postgres-operator`](../../../profiles/local-kubernetes-postgres-operator).

## Known limitations (experimental)

- No `backup-target` yet: CloudNativePG's built-in backup (Barman/S3) is not
  wired to the shared `backup-target` contract in this iteration.
- Single application database/owner is bootstrapped (via `initdb`);
  additional databases are not created.
- The password secret value must not contain characters that break the
  Helm-expanded YAML scalar (the renderer does not quote it); the chart's
  own secrets Secret does quote values properly.
