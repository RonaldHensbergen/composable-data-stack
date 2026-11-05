# Local Kubernetes PostgreSQL-operator example

The warehouse of the local stack runs on the experimental CloudNativePG
operator module
([modules-experimental/warehouse/postgres-operator](../../modules-experimental/warehouse/postgres-operator),
issue #768) instead of the Compose `postgres` module: same `sql-database`
contract, Kubernetes-only implementation, rendered to a Helm chart. Superset
binds to `postgres.sql-database` exactly as it does against the plain
postgres module — no consumer-side changes.

```bash
cds validate local-kubernetes-postgres-operator --target helm
cds test local-kubernetes-postgres-operator --target helm
cds render local-kubernetes-postgres-operator --target helm   # writes chart/
```

The rendered chart contains a CloudNativePG `Cluster` CR
(`templates/postgres-manifest-cluster.yaml`) plus the app-credentials Secret
it bootstraps from. CDS does not install the operator itself: install
CloudNativePG on the cluster out of band first, then deploy the chart (see
[docs/kubernetes.md](../../docs/kubernetes.md) and the module README):

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm upgrade --install cnpg cnpg/cloudnative-pg -n cnpg-system --create-namespace
PROFILE=profiles/local-kubernetes-postgres-operator/profile.yaml make k3d-up k3d-install
```

The `sql-database` endpoint is the operator-created `{{ .Release.Name }}-postgres-rw`
Service (declared under `implementation.kubernetes.services`), so
`cds maturity local-kubernetes-postgres-operator` reports the database
capability as bound by `postgres`.
