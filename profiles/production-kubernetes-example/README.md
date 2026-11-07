# Production-style Kubernetes example

The `local-dagster-postgres-superset` stack with the cache locked down, the
Istio service-mesh module enrolling Postgres, Dagster, and Superset for STRICT
mTLS, and a `prod` environment overlay that promotes the profile to CDS's
production security policy, rendered to Kubernetes (issues #77 and #1171).

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

The chart includes Istio `PeerAuthentication` and optional per-service
`AuthorizationPolicy` resources. Install istio-base and istiod out of band and
label the release namespace `istio-injection=enabled` before deploying for
traffic interception; the module renders the security resources but does not
install the control plane. See the [module README](../../modules/security/istio/README.md).
