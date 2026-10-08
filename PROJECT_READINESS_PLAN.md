# Project readiness and deployment review plan

Date: 2026-10-07. Baseline: main at 83a7320.
Status: **REVIEWED PLAN — fixes and cloud deployment are still pending.**

Owner-selected target: **A — private, single-user Cloud Run learning deployment,
existing SQLite checkpoints; defer shared PostgreSQL.** This replaces the preliminary
readiness draft, not the historical PLAN.md or IMPLEMENTATION.md.

## 1. Outcome, scope and deliverables

The owner must be able to build a verified image, deploy privately, create/revise/approve a
report, diagnose failures, replace a revision, roll back code, export reports and stop the service.

Cloud Run can replace an instance even with min/max one and session affinity. Local SQLite and
in-memory job ownership are not durable. BackgroundTasks is not a recoverable queue. Start with
disposable topics; never promise recovered checkpoints after replacement.

Scope: tracked application, frontend, tests, dependencies, Docker, CI, rollout scripts and docs.
Secrets, checkpoints, logs and environments stay private. No cloud state was inspected or changed.
This planning task makes no application edits, commits or deployments.

Deliverables:
- This architecture, ranked fixes, decision record and evidence ledger.
- [Owner-run GCP instructions](GCP_LEARNING_RUNBOOK.md).
- [Regression/acceptance test plan](REVIEW_TEST_PLAN.md).
- [Deferred production work](TODOS.md).

## 2. Foreground and background execution

This frontend polls GET requests; it does not stream tokens. This repository has no file upload,
document ingestion, embeddings or vector database. Those belong to the separate RAG project.

~~~mermaid
sequenceDiagram
    actor User
    participant UI as static/index.html
    participant API as main.py
    participant Worker as _run_graph
    participant Graph as graph.py
    participant Agents as agents.py
    participant Tools as tools.py / Tavily
    participant LLM as providers.py / Groq or Vertex
    participant DB as SqliteSaver
    User->>UI: Enter topic
    UI->>API: POST /research
    API-->>UI: 202 + thread_id
    API->>Worker: BackgroundTasks (worker thread)
    Worker->>Graph: invoke(initial state)
    Graph->>Agents: researcher_node
    Agents->>LLM: Decompose queries
    Agents->>Tools: Concurrent web searches
    Agents->>LLM: Synthesize notes
    Graph->>Agents: analyst_node → analysis
    Graph->>Agents: writer_node → draft
    Graph->>DB: Checkpoint at human_review interrupt()
    Worker-->>API: Worker exits; running flag cleared
    loop While processing
        UI->>API: GET /research/{thread_id}
        API->>DB: Read snapshot
        API-->>UI: Status / draft / sources / audit
    end
    User->>UI: Approve / revise / research gap
    UI->>API: POST /research/{thread_id}/review
    API->>Worker: Command(resume=decision)
    Worker->>Graph: Resume review node
    Note over Graph: Approve → finalize; revise → writer;<br/>gap → researcher → analyst → writer
    Graph->>DB: Save next interrupt or final report
    UI->>API: Poll final status
    API-->>UI: Markdown report
~~~

~~~text
Topic → app/static/index.html:startResearch()
└─ app/main.py:start_research() / ResearchRequest
   ├─ auth, limiter, config checks, UUID, job claim
   ├─ HTTP 202 (accepted, not completed)
   └─ BackgroundTasks → _run_graph()
      └─ app/graph.py:build_graph() / ResearchState
         ├─ app/agents.py:researcher_node()
         │  ├─ DecomposedQueries / decomposition prompt
         │  ├─ app/providers.py:get_llm()
         │  ├─ app/tools.py:run_multi_search() → run_search() → Tavily
         │  ├─ optional typesafe_client.py passage judgments
         │  └─ sub_queries, research_notes, sources, status=researched
         ├─ agents.py:analyst_node() → analysis, status=analyzed
         ├─ agents.py:writer_node() → draft, clear feedback, status=drafted
         └─ graph.py:human_review_node() → interrupt() / SQLite snapshot
            └─ main.py:review_research() → Command(resume=decision)
               └─ human_review_node() updates feedback, action, count, status
                  └─ route_after_review()
                     ├─ approved → finalize_node() → END
                     ├─ revise → writer → review
                     ├─ research_gap → researcher → analyst → writer → review
                     └─ revision_count > 3 → finalize with limit note → END
~~~

The first three rejections can trigger new passes; the fourth exceeds the cap and forces
finalization. Review expiry is checked on API access, not by a cleanup worker. The real
researcher retains prior sources but replaces sub_queries with the latest pass.

| Files | Responsibility and connections |
|---|---|
| app/main.py | FastAPI lifespan, schemas, auth/limiter, BoundedJobStore, background runner, polling/review, health/readiness, JSON logs; compiles graph with checkpointer. |
| app/graph.py | ResearchState, build_graph, human_review_node, route_after_review, finalize_node; topology and bounded loops. |
| app/agents.py | Prompt constants, DecomposedQueries, researcher/analyst/writer; state updates, delegates models/search/judgments. |
| app/tools.py | SearchResult, Tavily wrapper, concurrent searches and citation audit. |
| app/providers.py | Cached Groq/Vertex client factory, per-agent models, deadlines/retries. |
| app/config.py | dotenv/env settings, Secret Manager fallback, startup validation. |
| app/typesafe_client.py | Optional review intent, passage, citation and error judgments; fallbacks. |
| app/metrics.py / app/__init__.py | Process-local locked metrics/bounded latency; package marker. |
| app/static/index.html | HTML/CSS, vendored Markdown/sanitizer, submit/poll/render, source list, review buttons, separated progress segments. |
| tests/conftest.py | Placeholder credentials and isolated graph/job/provider fixtures. |
| tests/test_api.py | HTTP lifecycle, review/auth/error/timeout/restart/concurrency. |
| tests/test_agents.py / test_tools.py / test_typesafe.py | Mocked cognition, search isolation, citation and optional judgment fallbacks. |
| tests/test_config.py / test_providers.py | Config and real Vertex adapter option forwarding without live inference. |
| tests/test_deploy_vertex.ps1 / mock_gcloud.ps1 | Twelve mocked rollout scenarios, not live IAM verification. |
| scripts/deploy-vertex.ps1 | Existing-service candidate build, tagged smoke, promotion/rollback. Not first-service bootstrap; private IAM smoke needs work. |
| .github/workflows/ci.yml | Ubuntu lint/format/tests/coverage/rollout tests/audit, then Docker boot smoke. No deployment/CD. |
| Dockerfile | uv builder → non-root Python 3.13 runtime with copied venv, healthcheck, uvicorn; no runtime uv resync. |
| pyproject.toml / uv.lock | Direct dependencies and frozen resolution. |
| .dockerignore / .gcloudignore / .gitignore / .env.example | Build allowlist, local data exclusions and placeholder config. |
| scripts/gstack.ps1 / gstack-run.sh / gstack-env.sh / repair-gstack.ps1 | Local Windows review tools; not deployed application components. |
| README.md / AGENTS.md / CLAUDE.md / GSTACK_SETUP.md | Current guidance; preserve historical PLAN.md / IMPLEMENTATION.md as history. |

## 3. Ranked findings and implementation tasks

P1: required before learning-deployment acceptance. P2: next-stage hardening, required as
applicable before public/multi-user use. Failure scenarios are code-review findings,
not claims of observed production incidents.

| ID | Finding / evidence | Remedy and acceptance |
|---|---|---|
| R01 P1 | index.html renderResponse uses precision OR 1.0: zero displays 100% Grounded. URL matching is not factual verification. | Preserve valid zero; missing = not evaluated. Label URL provenance separately from semantic judgment. Test 0/fraction/1/absent/no citations; no hallucination-free claims. |
| R02 P1 | main.py _state_to_response synchronously calls audit_citations from async polling/review. TypeSafe network calls can repeat and take 30s. | Audit once per draft version in background, persist/version results; GET makes no judgment calls. Pending/failure explicit, URL-only fallback. Test slow audit vs health/poll responsiveness, repeated GETs and invalidation. Disable TypeSafe until fixed. |
| R03 P1 | agents.py saves title/url only; semantic audit expects content and uses link label as claim. Mock tests supply evidence the real path drops. | Preserve bounded evidence/provenance and surrounding assertion. Test real researcher→audit payload, unsupported claim and failure. Until then only honest URL provenance, semantic mode disabled. |
| R04 P1 | review_research reads snapshot before claiming execution. Delayed request can use stale snapshot after another run releases claim. | Claim before authoritative validation; release on all rejections/errors and validate review/draft version. Deterministic barrier test for a delayed review across a completed earlier decision. |
| R05 P1 | Job-store error eviction can remove oldest active claim; active jobs have no admission bound. HTTP concurrency does not cap ongoing accepted tasks. | Separate active claims from bounded finished errors; never evict running entries. Initial active-run cap one, clear overload response. Pressure/admission/release tests. |
| R06 P1 | Query output count/member length not enforced; fallback JSON may contain bad members. All failed searches still lead to synthesis without evidence. | Validate/trim/cap queries and total evidence; visibly fail when no usable sources, distinguish partial success. Test malformed/empty/all-failed/partial/follow-up limits. |
| R07 P1 | Whitespace topic, unbounded feedback and arbitrary/contradictory action accepted. | Validate stripped topic, bounded feedback and action consistency; preserve documented legacy no-action behavior. Test invalid and valid branches. |
| R08 P1 | loadThread sets busy only after fetch; delayed loads/start can steal currentThreadId/polling. Requests have no finite timeout. | Single operation owner, generation token and AbortController; stale response cannot render/steal timer. Retain ID/retry and restore controls on failure. Controlled overlapping-load/start/timeout tests. |
| R09 P1 | Public deployment plus optional auth exposes paid calls; UI sends no app key. Vertex GCP_PROJECT_ID can activate Secret Manager fallback for api-key despite empty API_KEY. | Fresh IAM-private learning service/proxy; anonymous denial plus lifecycle acceptance. Verify no unexpected app-key activation; explicit config semantics for Vertex. Never put shared server key in browser storage. |
| R10 P1 if using rollout script | Vertex script assumes existing hardcoded service/account; smoke lacks IAM token. Traffic comparison is not atomic with later mutation. | Parameterize/bootstrap separately, audience-correct private smoke, finite deadlines, serialize rollouts, record digest/config/secret versions and restore traffic. Mock private/first-deploy/rollback cases; owner-run proxy/manual deployment is the initial alternative, not script-fix evidence. |
| R11 P1 | Ready = config + graph, not real search/model success. SQLite/jobs may vanish on revision replacement. | Real approve/revise/gap smoke; export report; record deployment; practice disposable replacement and code rollback. Document data loss and recovery limits. |
| R12 P2 | Limiter trusts arbitrary key/XFF; local logs unbounded; raw topic/errors may be sensitive. | Verified-identity/trusted-proxy limits before public use; auth integration, safe errors, redaction/rotation/stdout policy and spoofing/privacy tests. |
| R13 P1 basic / P2 polish | Keyboard/tab semantics, reduced motion, narrow screens and busy/errors need browser validation. | Preserve technical UI/spaced pipeline; labels/focus/status announcements, reduced motion. Test 320px/desktop/200% zoom/long URLs, sanitizer and safe schemes. |
| R14 P2 | Coverage report without threshold/JS regression gate; injected audit tool unpinned; no CD. | Targeted JS and R01–R08 regressions; reproducible audit tooling; real paid smoke remains explicit/manual. Do not claim existing automated deployment. |

### Required implementation sequence

1. **Senior developer:** R04–R07 ownership, admission, validation and evidence failures,
   with regression tests alongside fixes.
2. **Senior tester:** R02/R03 background audit/evidence contract and full backend validation.
   Existing checkpoints lacking audit fields must remain readable and show not evaluated;
   no destructive local database migration.
3. **Senior frontend developer/tester:** R01/R08/R13 citation correctness, operation ownership,
   retry/accessibility and segmented pipeline. Exercise raw and rendered tabs.
4. **DevOps:** R09–R11/R14 private bootstrap/smoke, serial rollout, immutable image/secrets,
   cost/recovery/shutdown and current documentation.

Semantic R03 may stay disabled for the first demo only if honest URL labels and nonblocking
R02 behavior pass. P2 production controls remain open, never silently waived.

## 4. Verification and evidence ledger

| Area | Evidence now | Remaining gate |
|---|---|---|
| Application/core/config | Core modules and test contracts inspected | Fixes/regressions; no live-provider behavior inferred |
| Frontend | Static flow/render/source/markup inspected | Live visual, keyboard and controlled network testing |
| Dependencies/lint/format | Isolated frozen Python 3.13 setup; ruff lint/format passed | Repeat after implementation |
| Windows Python suite | Collection blocked by Application Control loading grpc cygrpc DLL | Linux alternative passed; never disable host security policy |
| Rollout PowerShell suite | 12 mocked scenarios passed | IAM-private/first-deploy acceptance |
| Linux full Python suite | 66 passed, 93% statement coverage, Python 3.13.11; isolated read-only app/tests mounts and placeholder credentials | Repeat after fixes; five Vertex adapter deprecation warnings |
| Docker production runtime | Image built; uid 1000 appuser, /health OK, /ready ready, UI HTTP 200; own smoke container removed | Real model/search smoke still owner-run |
| GitHub CI | Workflow inspected, not live run queried | Passing run at final implementation SHA |
| Dependency audit | pip-audit passed: no known vulnerabilities reported in isolated frozen review environment | Advisory snapshot only; repeat in Linux CI after changes |
| Documentation checks | All 20 current runbook PowerShell blocks parsed; checked wrapper stopped synthetic exit 9 and passed exit 0; Markdown fences/whitespace checked | Syntax/stop probe is not live command/IAM execution evidence |
| GCP/provider/cost | No live cloud state inspected | Owner verifies project/billing/models/policies and costs |

Mocked checks cannot prove live inference, IAM-private routing, durability or latency.
Python packages installed transitively do not imply their corresponding cloud services are consumed.

## 5. Failure and recovery registry

| Trigger | User behavior / operator response / regression |
|---|---|
| Missing secret/config | Startup/readiness failure; verify binding/version, never print values. |
| Quota/timeout/all searches fail | Visible failure/degraded evidence, not spurious review; inspect safe logs and retry new topic. |
| Duplicate/stale decision | Conflict/stale version; reload latest review. Barrier-based concurrency test. |
| Active cap reached | Retry-later; no unbounded queue. Pressure/release test. |
| Auditor unavailable | Pending/unavailable with honest fallback; review remains usable, no network in GET. |
| Poll/load network error | Controls recover, ID retained, explicit retry; stale response ignored. |
| Review expiry | Expired message, no accidental resume; export draft/start new run, no automatic deletion. |
| Instance/revision replacement | History/work may be lost; use exported final Markdown, rerun. No durable recovery promise. |
| Candidate smoke fails | Preserve old traffic where staged rollout supported; restore recorded split, inspect logs. |
| Code rollback | Restore prior code; not lost filesystem data. |
| Excess spend | Export and delete dedicated learning service; inspect remaining resource charges. |

~~~mermaid
flowchart LR
    Owner[Owner gcloud identity] --> Build[Cloud Build]
    Build --> Registry[Artifact Registry digest]
    Registry --> Run[Private Cloud Run learning service]
    Owner --> Proxy[Authenticated localhost proxy]
    Proxy --> Run
    Secrets[Secret Manager pinned versions] --> Run
    Run --> Models[Groq or optional Vertex AI]
    Run --> Tavily[Tavily search]
    Run --> DB[Instance-local SQLite and job flags]
    DB --> Loss[May disappear on replacement]
    Run --> Logs[Cloud Logging and Monitoring]
    Proxy --> Export[Owner saves final Markdown]
    Registry --> Rollback[Previous digest or revision]
    Rollback --> Run
~~~

Selected cloud services: Cloud Build, Artifact Registry, Cloud Run, Secret Manager, Cloud
Logging/Monitoring; external Groq and Tavily. Vertex optional. TypeSafe/LangSmith disabled
initially. The runbook's Cloud Storage bucket stages build source/logs, not checkpoints.
Cloud SQL, Firestore, Pub/Sub, Cloud Tasks, embeddings and vector storage are not used here.

## 6. Decisions and production triggers

| Decision | Basis and consequence |
|---|---|
| SQLite, private single-user demo | Owner's explicit A; disposable history |
| Fresh multi-agent-research-learning service | Avoid mutating any unknown existing service |
| Groq first; optional Vertex | Lower bootstrap effort; verify live quota/model availability |
| Codex-only independent reviewers | Honor execution preference; no cross-provider consensus |
| Preserve current visual direction | Fix correctness/recovery/accessibility, not a redesign |
| TypeSafe disabled until audit fixes | Avoid blocking polling and false grounding confidence |
| Active run cap one initially | Instance and HTTP limits do not bound accepted graph tasks |
| Manual deploy, no CD | Existing CI only verifies |

Set an owner-chosen monthly learning budget and end date before enabling resources.
Idle min-instance/always-allocated CPU costs money. Budget alerts notify, not enforce a spend cap.
Measure representative duration, model tokens/calls and search usage, then estimate reports plus
idle/build/storage costs. Stop instead of increasing concurrency blindly.

Upgrade checkpoints AND job ownership/retries to shared durable storage before a second user,
valuable retained history, interrupted-run recovery or multiple instances. Before production
infra investment, measure quality/time/cost against a simpler prompt baseline.

## 7. Independent review record

Strategy review: native Codex agent Bacon completed frozen input SHA
c4ada9878d41a7c59893bce9afafa2ff6f27d33fa36ef918b059d55db0765387.
Seven accepted findings: operator exercises, explicit IAM/bootstrap, honest evidence,
workload/cost/stop limits, durability/export triggers, provider/tooling alternatives, and
product-value measurement before production infra.

Original autoplan restore is retained under ignored .gstack/state.
Owner decisions: A architecture and declined telemetry. Technical remedies above are proposed,
not individual approvals. Application implementation is pending.

Full mechanical gstack protocol is not claimed: external reviewers are disabled by Codex-only
execution, formal Office Hours design artifact was not completed, and review coverage/scores
must reflect actual work. Native design, DX and engineering reports follow below.

### Native design review — completed, eight findings accepted

Reviewer: Archimedes (Codex). Seven design dimensions assessed through static code/plan review;
no live browser session, visual score or outside-provider consensus. Linked companion documents
were being written during this review and are now part of the final review input.

These requirements extend the tasks above and are part of the implementation gates:
1. **R08/P1 review ownership:** bind visible draft, current thread and submitted decision to one
   operation/thread/draft version. Claim busy state before load changes identity; disable old
   review controls synchronously. Delayed load B while draft A is visible must never send a
   review of unseen B. Ignore stale errors as well as stale successes.
2. **R08/P1 uncertain writes:** request abort is not server cancellation. Reconcile review state
   by GET before retry; preserve revision version. Add an idempotency/submission token for
   initial POST or show explicit outcome-unknown and require reconciliation rather than
   automatic duplicate submission. Do not silently retry non-idempotent writes.
3. **R08/P1 recovery:** keep last draft, thread ID and unsent feedback across errors. Load/poll
   must consistently expose generation failure, network failure, expiry, conflict, overload and
   missing-thread states, with useful actions. Expired drafts remain readable/exportable.
4. **R01/R02/R03/P1 audit contract:** method, state and draft version are explicit. Replace
   “Grounding Verifier”, “Hallucinations”, “Verified Sources”, “grounded draft” and automatic
   “Finalized & Audited” claims when unsupported. Use retrieved sources/URL match labels;
   zero citations is not evaluated, not 100% factual grounding. Invalid-only source list has
   an explicit empty state.
5. **R15/P1 truthful progress:** checkpoints describe completed nodes, not always current work.
   Expose reliable current stage or neutral “regenerating” for unknown/research-gap transitions.
   Keep five nodes/four spaced connectors; network loss/failure/restart is not completion.
6. **R15/P1 terminal reason/export:** distinguish user-approved from forced revision-cap
   finalization in response/UI. Warn before exhaustion; forced result includes warning in saved
   Markdown. Define Raw-tab copy/save action as the initial export procedure; no nonexistent
   automatic archive/download feature claimed.
7. **R13/P1 basic accessibility:** associated visible input labels, keyboard review/tab operation,
   selected-tab/panel semantics, visible focus and nonspammy status announcements are required
   for demo acceptance. Restore useful focus without stealing it on polls. Reduced-motion
   suppression and rendered contrast/touch-target checks included.
8. **R13/P2 responsive content:** explicit title/tab wrapping, grid minimum sizing and contained
   table/code scrolling; test long titles/URLs/unbroken text at 320px/tablet/desktop/200% zoom.

Native report inspected the whole frontend/API/graph plus targeted agents/tools/tests.
It reused existing visual tokens and sanitizer. No redesign or application edit was performed.

### Additional inspection notes

- Extend R02 to main.py review intent classification: the API can synchronously call optional
  TypeSafe before dispatch when action is absent. Move inference into background graph work;
  explicit validated actions require no semantic call. The graph already has an intent fallback.
- **R16/P1 build context:** .gcloudignore is an application-only allowlist, but .dockerignore is
  broader and lacks .gstack/, .env.* and general SQLite exclusions. Harden context exclusions
  and test the send list. Current Dockerfile copies specific inputs; no secret in the final image
  has been demonstrated. Context privacy and final image privacy are separate checks.
  Also cover .env.* and nondefault checkpoint filenames in Git exclusions, preserving the
  tracked placeholder .env.example. Do not delete private local files to make a check pass.
- **R17/P2 provider maintenance:** the Linux suite emits ChatVertexAI deprecation warnings
  (LangChain 3.2, announced removal at 4.0). Plan a tested provider-factory migration before
  upgrading that dependency; existing adapter deadline/structured-output tests must continue
  passing. Do not make a blind import replacement in this planning task.
- Docker build's shell-CMD lint warning is recorded, not a demonstrated signal failure:
  current CMD explicitly uses exec uvicorn. A future JSON-form wrapper must preserve dynamic
  PORT behavior; signal/shutdown testing is preferable to mechanical replacement.
- Current CLAUDE.md incorrectly calls review timeout unenforced and config import side-effect-free;
  config can perform Secret Manager reads on import. Correct those assertions, main.py's
  persistence-across-instances comment, and stale endpoint/graph claims with R11 documentation.

### Dependencies and implementation coordination

Direct runtime packages in pyproject.toml: FastAPI (HTTP/schema integration), uvicorn[standard]
(ASGI server), LangGraph (workflow), langgraph-checkpoint-sqlite (checkpoint persistence),
LangChain (message/model orchestration), langchain-groq and langchain-google-vertexai (LLM
adapters), langchain-tavily (web search), python-dotenv (local config), Pydantic (typed inputs),
SlowAPI (local rate limits), google-cloud-secret-manager (optional secret fallback), typesafe-sdk
(optional semantic judgments). Dev packages: pytest, pytest-asyncio, pytest-cov, httpx and ruff.
uv/Google Cloud CLI/Docker/gstack are tools, not additional application services.

Do implementation in isolated branches/worktrees if multiple engineers work simultaneously.
main.py/graph.py/API contracts are coupled: one owner merges R02/R04/R05/R07 before frontend
integration. Query validation and evidence retention can be isolated in agents.py/tools.py only
after the audit contract is agreed. Frontend depends on response version/method/status/terminal
reason; do not mock a different contract than production. Runbook/CI work can proceed separately,
then final engineering/acceptance runs against the combined tree. No such branches or edits were
created for this planning review.

## 8. Implementation checklist and completion criteria

Every task below is pending; findings have been addressed in the PLAN, not fixed in code.
Effort depends on unfamiliarity with cloud IAM and UI integration; budget several focused
implementation sessions plus a separate owner-run deployment session, rather than promising
unmeasured agent-speed estimates.

- [ ] R04/R05: enforce checkpoint-version ownership and single-run admission; deterministic concurrency tests.
- [ ] R06/R07: validate topics, feedback/actions and bounded queries; handle no usable evidence.
- [ ] R02/R03: background/versioned audit with real bounded claim/evidence contract and no external work in polling.
- [ ] R01: correct zero/missing precision and all confidence labels; test actual UI rendering.
- [ ] R08: operation ownership, deadlines, uncertain-write reconciliation, retained draft/feedback and retry.
- [ ] R15: truthful progress, terminal finalization reason/cap warning and documented export.
- [ ] R13: basic accessible controls/tabs/focus/status; responsive/long-content browser acceptance.
- [ ] R09: explicit app-key configuration and private IAM bootstrap/proxy acceptance.
- [ ] R10: parameterized private candidate smoke, serial rollout and immutable deployment manifest.
- [ ] R11: real lifecycle, export/replacement/code-rollback/shutdown drills and current docs.
- [ ] R12/R14/R16/R17: public-auth/log hardening, CI regression/reproducibility, context privacy and provider maintenance as scoped.

Acceptance for implementation: backend/PowerShell/frontend gates pass against the final tree;
Docker boots non-root; exact GitHub commit passes CI; operator prerequisites and live private
lifecycle pass; export/rollback/shutdown are understood; remaining P2/deferred risks acknowledged.
Do not label the application deployment-ready merely because the current 66 tests pass.

### Native developer-experience review — completed, nine findings accepted

Reviewer: Avicenna (Codex); eight DX lenses considered, static plan/source/SDK inspection,
no live onboarding measurement or outside voice. Remedies integrated into runbook and gates:
1. Custom build SA now has Logging Writer for default LEGACY dual logging; bucket-only mode
   requires explicit GCS_ONLY YAML, not merely --gcs-log-dir.
2. Checked-command helper embedded in main-terminal blocks, including quality gates. Failure
   injection must prove failed native commands stop later steps.
3. README must lead to this private runbook, preserve .env, show server startup and quarantine
   public/latest recipes and unsupported private Vertex script usage.
4. Self-contained proxy terminal initializes its own project/account/region, handles port conflict.
5. AGENTS/README must use disabled dotenv/placeholder testing and frozen dependency execution.
6. Promote R16 Docker context privacy to **P1**: exclude .gstack/.context/.claude/.env.*/SQLite;
   verify context separately from final image and gcloud upload.
7. Ten-minute operator expiry is not cancellation: inspect known thread, never submit new work
   while it runs; abandoned disposable worker requires explicit export/stop/redeploy procedure.
8. Vertex migration includes full intended configuration, clears old bindings/overrides, checks
   effective models and requires explicit-auth R09 gate. No implicit “ready proves inference”.
9. Add prerequisite versions/install links, IAM API, bootstrap handoff/resumption and observable
   service-absence/resource inventory at shutdown.

Scoped bucket role combination was not found demonstrably broken; validate live without inventing
a permission defect or granting project-wide Storage Admin. Existing design choices preserved.

### Final engineering review — completed by primary Codex

The independent final agent's report was unavailable after interruption; no completion or
consensus credit is assigned. Primary Codex completed these four engineering passes:

- **Architecture:** retain lifespan compilation, SQLite saver, existing agents/provider factory
  and private manual deployment. Merge versioned decision ownership/admission first; always
  release claims after validation errors. IAM does not make BackgroundTasks durable or cancellable.
- **Code quality:** keep deterministic validation around semantic judgments. Reuse the graph's
  existing intent fallback, removing HTTP inference duplication. Typed, versioned audit data
  replaces serialization-triggered external calls. Preserve search ordering and DOMPurify.
- **Tests:** 66 Python tests/93% coverage and 12 rollout checks establish the baseline, not new
  fixes. Required regressions cover stale reviews, active-job pressure, actual claim/evidence,
  no-network polling, zero scores, uncertain writes, IAM-private smoke and context exclusions.
- **Performance:** bound queries/evidence/active runs, move TypeSafe off HTTP handlers and avoid
  per-poll audit cost. HTTP concurrency eight does not allow eight graph jobs or guarantee a
  spend ceiling. Measure representative work before tuning.

Concrete implementation contracts:
1. Add opaque review_version to awaiting-review responses and require it on decisions. Claim,
   reread authoritative checkpoint, compare version, reject missing/stale versions explicitly.
   Update UI/examples/mocks together; legacy no-action semantics can remain with valid version.
2. Audit carries method, status and target draft version. A background audit stage after writer
   and before human_review is proposed; retain five visible UI steps. Old snapshots show not
   evaluated. Finalization's cap note must not pass off an old draft audit as new-text verification.
3. First private demo uses explicit initial-submission-outcome-unknown handling, not blind POST
   retry. Preserve time/topic context; owner can recover accepted thread ID from private logs
   and load it. Known review retry reconciles ID/version first. Durable write idempotency is
   deferred until public/multi-user use.
4. Extend R09: wildcard CORS plus an authenticated localhost proxy needs a browser-origin
   boundary check. Configure specific proxy origin; test untrusted preflights and simple writes.
   This is a code-driven risk inference, not an observed exploit. CORS is not authentication.
   Keep proxy local and close after use. [FastAPI CORS reference](https://fastapi.tiangolo.com/tutorial/cors/).
5. Secret metadata checks are administrator actions or scoped metadata-viewer grants; do not
   grant key-value access merely to list versions or inventory resources.

Cross-phase themes: strategy/DX require observable setup, costs and recovery; design/engineering
require honest audit/version/progress and preserved drafts; DX/engineering require isolated
verification, context privacy and rollback distinct from data recovery.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---|---|---|
| Strategy | autoplan | Learning scope/outcome | 1 native | Completed substantive review | 7 findings integrated; A preserved |
| Design | UI scope | Interaction/accessibility | 1 native | Completed static review | 8 findings integrated; no live visual QA |
| DX | operator runbook | Setup/deploy/recovery | 1 native | Completed static review | 9 findings integrated; no onboarding measurement |
| Engineering | final plan | Architecture/code/tests/performance | 1 primary; native attempt unavailable | Primary review completed | R01–R17 consolidated; contracts/boundary checks |
| Outside provider | Codex-only execution | Cross-provider opinion | 0 | Disabled | No consensus claimed |

OUTSIDE COVERAGE: Claude Code disabled throughout. Final native engineering report unavailable
after interruption; no completion credit. Unknown model identities remain unknown.
Full gstack Office Hours/snapshot/log/interactive-approval protocol was not completed; the saved
substantive plan is the deliverable, not formal ship clearance.

VERDICT: Repository review plan completed and reviewable. P1 implementation, combined-tree
checks, live UI and owner-run private cloud acceptance remain required before launch.
No application changes, cloud mutations, commits or pushes performed.

**UNRESOLVED DECISIONS:**
- Owner supplies project/account, available model, learning budget and end date before deployment.
- Final plan approval and scope overrides remain owner decisions; remedies are proposed, not individual approvals.
