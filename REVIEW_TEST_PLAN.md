# Review regression and operator acceptance test plan

Baseline inspected: 83a7320, 2026-10-07. These are planned acceptance cases for the fixes,
not claims that new tests already exist. Mock every external provider in automated checks.
Use isolated memory/temp SQLite, never the owner's checkpoints or real keys.

## Developer and backend gates

| Case | Files / implementation | Required regression |
|---|---|---|
| Ownership across stale snapshot | main.py review_research, graph.py; test_api.py | Hold request A after snapshot acquisition, let B resume and finish, release A; A must not affect new checkpoint. Validate thread/review version and release claims on all exceptions/rejections. |
| Active store pressure/admission | BoundedJobStore; test_api.py | Oldest entry running while error history fills; active claim retained, finished history bounded. Initial run cap one, predictable overload, slot released on success/error/interrupt. |
| Topic/review/query validation | main.py/agents.py; test_api.py/test_agents.py | Whitespace topic, excessive feedback, arbitrary/contradictory action, malformed query JSON, non-string/empty/overlong/more-than-cap queries. Valid legacy payloads still route correctly. |
| Search evidence failure | agents.py/tools.py; test_agents.py/test_tools.py | All fail/empty → explicit failed/degraded run, never unsupported successful draft; partial evidence retains provenance; total budget holds across follow-up. |
| Audit once, nonblocking | main.py/graph.py/tools.py; test_api.py/test_tools.py | Slow mocked auditor plus simultaneous GET health/poll; no event-loop starvation or external judgments per GET. Same draft cached; new draft invalidates; exception shows unavailable/fallback. |
| Semantic input accuracy | agents.py/typesafe_client.py/tools.py; test_agents.py/test_tools.py | Actual researcher output passes bounded source evidence and real surrounding claim, not “source” link label; unsupported claim/missing evidence/failure handled honestly. |
| Checkpoint compatibility | graph.py/main.py; test_api.py | Existing snapshot without new audit/version fields loads safely without live synchronous audit; same-file local restart works, instance replacement is not falsely claimed durable. |
| Explicit auth/secret config | config.py/main.py; test_config.py/test_api.py | Empty/unset app key with GCP project and optional api-key fallback; deliberate disable unambiguous. No secret/error leakage; protected endpoints behave as documented. |
| Terminal semantics | graph.py/main.py; test_api.py | Approve reason distinct from forced cap; three extra passes then fourth rejection finalizes with warning. Expiry does not erase available draft. |

## Frontend interaction gates

Use controlled delayed network responses in a browser/JS harness; assertions must exercise actual
frontend functions rather than mirror their calculations.

- Citation values: zero, fraction, one, missing, invalid, no citations, pending/failed audit,
  URL-only fallback, semantic judgment and stale draft version. Labels never imply factual proof.
- Delayed load B while reviewing A, then review/start/load C: identity and visible draft remain
  consistent; stale responses/errors cannot render, submit wrong review or steal timer.
- Drop POST response after server acceptance: abort does not mean cancel; initial submission
  outcome unknown/idempotency flow explicit; review reconciles via GET before retry.
- Network outage, provider failure, conflict, overload, expiry and missing thread behave consistently
  through both load and polling; preserve draft/ID/unsent feedback, recover controls and retry.
- Initial/gap/writing revision/approve/cap/restart paths use truthful status text and five nodes/four
  separate connectors. No fabricated stage, token stream or percentage promise.
- Raw and rendered tabs accessible by keyboard with correct selection/panel semantics.
  All inputs have associated labels; focus visible; status announcements are useful without poll spam.
- Preserve Markdown sanitization, HTTP(S)-only sources and safe text error rendering.
  Malicious Markdown/source title/URL/error string never executes.
- 320px/tablet/desktop/200% zoom, long title/URL/code/table/unbroken text: no page-wide overflow;
  readable controls; table/code scrolling confined. Reduced motion suppresses animation.
- Report export via Raw-tab copy/save preserves Markdown, citations and forced-limit warning.

## DevOps and rollout gates

- Full frozen lint/format/Python suite, existing PowerShell tests, targeted frontend cases.
- Production image builds, runs non-root and passes health/readiness using placeholder secrets;
  no files from .env/checkpoints/logs/.venv/gstack state in build upload.
- Add private IAM candidate/main smoke with correct audience and finite deadline; app API-key
  tests do not substitute for this.
- Test bootstrap absent service separately from existing-service candidate promotion.
- Mock candidate smoke failure, promotion/rollback failure, stale/concurrent rollout and owned-tag
  cleanup; serialize mutation instead of claiming compare-and-set atomicity.
- Deployment manifest pins image digest and numeric secret versions and preserves prior traffic;
  never prints key material.
- Fail each native quality/bootstrap/build/deploy command in turn; checked wrapper must stop
  later stages and preserve exact failure status. Exercise proxy in a genuinely new terminal
  with independent nonsecret variables/account; verify port-conflict recovery.
- Verify the custom build account's actual logging mode and both expected log destinations;
  bootstrap resumption inspects existing owned resources without deleting collisions.
- Vertex migration removes old Groq secret bindings and deliberate per-agent overrides, then
  checks effective model/config and live inference after explicit-auth semantics pass.
- Check local proxy browser-origin boundaries: intended/untrusted-origin preflight, simple
  writes and alternate port. CORS is not authentication; do not expose the proxy to the network.

## Owner-run paid acceptance (explicitly not automated mock evidence)

Through IAM-authenticated proxy, after confirming anonymous access denied:
1. Health/readiness show expected provider/config.
2. New report reaches draft with real source evidence.
3. Writing revision returns to writer/review.
4. Research gap returns to researcher/analyst/writer/review.
5. Approval finalizes; save raw Markdown privately.
6. Provider quota/failure diagnostic is understandable; controls recover.
7. Record representative latency, model/search usage and cost.
8. Export, drain active work, replace revision; expect disposable SQLite history may be gone.
9. Restore known-good code traffic and recheck; do not assert restored data.
10. Set min zero or delete exact dedicated service after export; verify remaining storage charges.

Live providers, IAM and billing have not been checked in this planning session.
