# Local Kubernetes example

The `local-dagster-postgres-superset` stack rendered to Kubernetes instead of
Docker Compose (issue #77), with the Istio service-mesh provider enrolling
Postgres, Dagster, and Superset for STRICT mTLS. CDS picks the render target at
the CLI:

```bash
cds validate local-kubernetes-example --target helm
cds render local-kubernetes-example --target helm   # writes chart/
```

The Istio control plane (istio-base and istiod) must be installed out of band
before applying the chart. The k3d E2E harness installs a pinned Istio chart
version and checks that the mesh policy is installed with the workloads; it
does not enable sidecar injection. For traffic interception, label the release
namespace `istio-injection=enabled` before deploying. See the [Istio module
README](../../modules/security/istio/README.md) for the operator steps and
boundaries.

Run it on the isolated local k3d cluster:

```bash
make k3d-build                                        # build module images
PROFILE=profiles/local-kubernetes-example/profile.yaml make k3d-up k3d-install
```

See [docs/kubernetes.md](../../docs/kubernetes.md) for the full workflow
(secrets at install time, translation boundaries, network policies,
troubleshooting).
