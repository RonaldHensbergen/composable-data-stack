# Production-style Kubernetes example

The `local-dagster-postgres-superset` stack with the cache locked down and a
`prod` environment overlay that promotes the profile to CDS's production
security policy, rendered to Kubernetes (issue #77).

```bash
# Local-style render (cache password required by the base profile)
cds validate production-kubernetes-example --target helm
cds render production-kubernetes-example --target helm

# Production-style render: prod overlay + Helm target
cds validate production-kubernetes-example --environment prod --target helm
cds render production-kubernetes-example --environment prod --target helm
```

The `prod` overlay (`environments/prod.yaml`) sets SCRAM host authentication
on Postgres (E139), enables the encrypted pgBackRest backup capability, and
declares the encrypted backup policies per workload class; see
[docs/production-hardening-checklist.md](../../docs/production-hardening-checklist.md)
for the full operator verification procedure and
[docs/kubernetes.md](../../docs/kubernetes.md) for the Kubernetes workflow.
