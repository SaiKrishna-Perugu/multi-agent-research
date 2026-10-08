# Deferred work from the readiness review

These are explicit future requirements, not completed implementation.
Keep the owner-selected SQLite learning deployment; do not silently expand scope.

| Work | Trigger / reason deferred | Acceptance before use |
|---|---|---|
| Shared PostgreSQL checkpointer + durable job ownership | Second user, valuable retained history, interrupted-work recovery or multiple instances | Migration/backups, idempotent retry/lease/ownership, replacement/recovery/failover tests. DB alone is insufficient. |
| Public end-user authentication + shared rate limiting | Any public/multi-user access | Verified identities, UI flow, trusted proxy/header policy, abuse/admission/cost controls; no shared key in browser storage. |
| Durable write idempotency | Before public/multi-user write retries | Persist deduplication/outcomes across instance replacement; conflict/replay tests. Initial private demo reports unknown outcomes instead of automatic POST retries. |
| Cloud Tasks/worker infrastructure | Reliable async delivery required | Durable queue/worker contract, authenticated internal endpoints, retries/deadlines/idempotency; not currently consumed. |
| Production observability/privacy | Real user topics or reliability commitments | Sanitized structured stdout/log retention, cross-instance metrics, run/audit tracing and alerts with SLOs. |
| Automated CD / Workload Identity Federation | After repeatable manual deploy and recovery acceptance | No long-lived keys, serial candidate rollout, private live smoke, approvals, immutable manifest and rollback drill. Existing CI has no deploy. |
| Semantic citation judgments | After R02/R03 evidence/nonblocking gates | Actual claim/evidence payload, honest method/version/status, measured failure/latency/cost; keep disabled until then. |
| Broader mobile/visual polish | After P1 interaction/accessibility correctness | Live responsive/zoom/long-content/contrast/touch QA without redesign drift. |
| Product comparison | Before more production infrastructure | Compare report usefulness, factual support, human revisions, time/cost against simpler baseline. |
| Full cross-provider gstack protocol | Only if owner changes Codex-only execution preference | Explicit outside reviewer output; never infer consensus from multiple native agents. |

P1 findings remain in PROJECT_READINESS_PLAN.md; moving a requirement here does not waive a gate.
