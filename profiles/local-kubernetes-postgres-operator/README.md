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
it bootstraps from. CDS does not install the operator itself: the module
declares it under `implementation.kubernetes.operators`, so `cds render` /
`cds test` warn with **W130** and `cds up --target helm` fails closed with
**E155** until the operator's CRD is on the cluster. Install it out of band
first, then deploy the chart (see
[docs/kubernetes.md](../../docs/kubernetes.md#operator-managed-modules) and
the module README):

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm repo update
helm upgrade --install cnpg --namespace cnpg-system --create-namespace cnpg/cloudnative-pg
kubectl get crd clusters.postgresql.cnpg.io   # verify
PROFILE=profiles/local-kubernetes-postgres-operator/profile.yaml make k3d-up k3d-install
```

The `sql-database` endpoint is the operator-created `{{ .Release.Name }}-postgres-rw`
Service (declared under `implementation.kubernetes.services`), so
`cds maturity local-kubernetes-postgres-operator` reports the database
capability as bound by `postgres`.
