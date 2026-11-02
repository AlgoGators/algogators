# infra/

Deployment topology — how the things in `apps/` and `services/` end up running
somewhere.

| directory | contents |
|---|---|
| `compose/` | Docker Compose stacks for local dev and the single-box prod deploys |
| `terraform/` | cloud resources: EC2, RDS, S3, DNS, IAM |
| `k8s/` | manifests, if and when anything outgrows Compose |

Kept separate from service directories on purpose. A service directory answers
"what is this code"; `infra/` answers "where does it run" — and those change for
different reasons, under different review.

Per-service **test** fixtures do *not* live here. `services/data-ngin/compose.test.yml`
belongs with the service, because its `just test` target brings it up.
