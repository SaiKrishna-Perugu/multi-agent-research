<!-- /autoplan restore point: ~/.gstack/projects/SaiKrishna-Perugu-multi-agent-research/main-autoplan-restore-20260822-165034.md -->

# Repo Credibility Plan

**Objective:** make this repo win an interview screen for the AI/ML Engineer JD it targets.
Every task below is scored against that, not against a design rubric.

Superseded the 20-task Web Studio remediation plan on 2026-08-22 after `/autoplan`'s CEO
phase. Two independent voices (Codex, blind Claude subagent) failed the prior plan 6/6 on
consensus with zero disagreements. The restore point above has the original verbatim.

---

## Why the plan changed

The prior plan assumed the UI was this project's biggest weakness. Verified facts say
otherwise:

| Fact | Evidence |
|---|---|
| The UI root route is **404 right now** | `main.py:120` is `@app.get("/home")`, not `/` |
| The README never invites anyone to open the UI | Usage section is curl only. No screenshot, no URL |
| The README advertises a limitation that no longer exists | `README.md:139` claims `MemorySaver`; `main.py:61` uses `SqliteSaver` |
| Three different test counts are in circulation | README says 20 (twice), the old plan said 21, `pytest --collect-only` says **23** |
| `CLAUDE.md` documents behavior the code no longer has | It says config raises at import time; validation now lives in `validate_llm_config()` / `validate_search_config()` at `config.py:90,100` |
| The model default changed without documentation | `GROQ_CHAT_MODEL` is now `openai/gpt-oss-120b`; the UI hardcodes "Groq (LLaMA-3.3-70B)" at `index.html:711` |

The rarest thing this repo does is a LangGraph `interrupt()` that genuinely suspends across
HTTP requests and survives a process restart. That is hard to fake and hard to find in other
portfolios. It is currently visible only to someone who runs three curl commands, 40 seconds
apart, with their own API keys. Nobody will.

---

## Phase A: make the repo tell the truth

Highest value per minute in the entire plan. None of it was in the prior version.

### A1 — Reconcile the working tree
7 modified files and 5 untracked paths, uncommitted for 16 days against a single commit.

- Decide the `/` to `/home` rename: restore `/` or update README, `CLAUDE.md`, and the
  proposed root test to match. **A public repo whose documented entry point 404s is the
  worst single item here.**
- Decide the `GROQ_CHAT_MODEL` default change. If `openai/gpt-oss-120b` is intended, update
  `.env.example`, the README, and `index.html:711`.
- Decide `package.json` / `package-lock.json`. They are `{}` plus a stub lockfile in a
  uv-managed Python repo. Evidence of an abandoned start. Delete or explain.
- Land as several scoped commits, not one. A public repo with one commit titled
  "initial commit" reads as a dump.

### A2 — Fix the README's false claims
- Remove the `MemorySaver` bullet at line 139. SQLite persistence shipped.
- Correct the test count to 23 in both places (lines 104, 184).
- Keep the honest remaining limitations: `REVIEW_TIMEOUT_MINUTES` unenforced, metrics not
  aggregated across instances.

A "here is what is wrong with my own work" section is what senior reviewers read to gauge
self-awareness. Yours currently proves the docs are not maintained.

### A3 — Update `CLAUDE.md`'s config section
It states config raises `RuntimeError` at import time and that this is why config errors
surface as import failures. That is no longer true. Either restore import-time validation or
rewrite the section.

---

## Phase B: make the differentiated claim visible

### B1 — Screenshot or GIF of the Web Studio in the README, with the URL
Thirty minutes. Does more for the UI's value than the entire cut list below.

### B2 — Recorded pause, restart, resume
A terminal cast or GIF: start a report, kill the server, restart it, resume the same
`thread_id` from SQLite, finalize. **The restart is the part that separates this from every
other multi-agent demo on GitHub.** Nothing currently lets a reviewer see it.

---

## Phase C: the code fixes that survive

Six tasks. Original IDs kept for traceability to the design review.

### T5 — Close the injection paths (P1)
`marked.parse()` at `index.html:1046` renders LLM output as raw HTML with no sanitizer. The
content path is scraped page → Tavily → LLM → `innerHTML`. Line 1065 interpolates `s.url`
into an `href` unescaped. `escapeHtml` at 1085 uses `innerText` → `innerHTML`, which does not
escape quotes, so it was never attribute-safe.

Two approaches, decided in Phase 3 below:
- **Vendor** `marked` + `dompurify` into `app/static/vendor/`, sanitize, build anchors with
  `createElement`/`setAttribute`, validate URL scheme.
- **Drop `marked`** and render via `textContent`. Eliminates the entire XSS class in ~10
  lines, removes three CDN dependencies, and needs no Node toolchain. Loses rendered
  markdown.

Correction to the prior plan: this is **not** localhost-only. `README.md:127-134` documents
`gcloud run deploy --allow-unauthenticated`.

Then write two sentences in the README about prompt-injection-to-XSS in agent UIs. That
paragraph is worth more to an interview than the fix.

### T3 — Example pills become real buttons (P1)
`index.html:740-743` are `<div onclick>`. Four primary entry points, mouse-only. The tabs 70
lines below are already `<button>`. One-word change.

### T4 — Focus ring on the review textarea (P1)
`outline: none` at line 433 with no `:focus` rule, immediately before two irreversible
actions. Two lines.

### T7 — Force the rendered tab before loading and error states (P2)
`switchTab('raw')` hides `#reportDisplay`, where every spinner and error is written. Launch
from the Raw tab and the button looks dead for 40 seconds. One line.

### T8 — Surface the server's `detail` string (P2)
Lines 961, 1001, 1031 throw `HTTP error! status: N`, discarding sentences FastAPI already
writes. Small, and it improves code a reviewer actually reads.

### T14 — Return `topic` on `ResearchResponse` (P2)
`topic` is in `ResearchState` but not the response, so the report headlines a UUID fragment
and `loadThread()` cannot recover it. Fixes a real API-design smell in `main.py`, which is a
file reviewers open.

---

## Cut, with reasons

| Cut | Why |
|---|---|
| T1a / T1b (progress) | T1a is throwaway by the prior plan's own admission. T1b likely breaks on Cloud Run, where CPU is throttled outside request handling and instances scale to zero. Resolved: keep the tracker honest by hiding it during the wait. No async rework. |
| T2, T11 (mobile) | Pixel-measured work for a 375px viewport that will not load a JD-targeted portfolio repo. |
| T6 (auth UI) | Auth is off by default, nothing is deployed, and the proposal put an API key in `localStorage`, which normalizes browser-side secret handling. |
| T9, T10, T12, T13 | Real defects, low signal for this artifact's purpose. |
| T15, T16, T17 | Same. |
| T18, T19, T20 (craft) | SVG icon sets and spacing tokens on a single-page demo. T19 argues the copy is "written for a portfolio reviewer, not a user" — but a portfolio reviewer is the only audience. |

All remain in the design review's task JSONL if priorities change.

---

## Resolved decisions

The prior plan shipped with six open decisions at the bottom, two of which determined whether
Phase 1 work was throwaway. All six are now closed:

| Decision | Resolution |
|---|---|
| Real progress or indeterminate? | **Indeterminate, honest.** Hide the tracker during the wait. No T1b. |
| Return `topic` on the response? | **Yes.** T14 stays. |
| Where do review controls live? | **Unchanged.** Cut with the rest of the layout work. |
| Is auth ever turned on? | **Not in scope.** Nothing is deployed. |
| Mobile pipeline strategy? | **None.** Mobile cut entirely. |
| Own the palette or accept Tailwind default? | **Accept.** Cosmetic, zero portfolio signal. |

---

## Effort

| Phase | Human | Claude Code |
|---|---|---|
| A (repo truth) | ~1h | ~15min |
| B (demo artifacts) | ~1.5h | manual, needs a real run |
| C (six fixes) | ~1.5h | ~25min |

Prior plan: ~26h human. This one: ~4h, and both voices score it higher against the objective.

---

## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | 0 | Skip Phase 3.5 (DX) | Mechanical | P3 | 5 "API" hits all describe internal response shape. No SDK, CLI, or install story | Running a DX phase |
| 2 | 1 | Mode = SELECTIVE EXPANSION | Mechanical | autoplan override | Iteration on an existing system | EXPANSION, HOLD, REDUCTION |
| 3 | 1 | Approach C (floor + evidence) | Taste | P1, P2 | Only option addressing the premise the plan never defended | A (ship as written), B (floor only) |
| 4 | 1 | Reframe accepted | **User Challenge** | n/a | User confirmed at premise gate; never auto-decided | Keeping all 20 tasks |
| 5 | 2 | Skip Phase 2 (Design) | Mechanical | P3 | Full 7-pass design review of this exact file completed 1h prior and produced these tasks. Accepted scope cut design work to 3 two-line fixes | Re-running dual design voices |

---

## Phase 3 corrections (eng review, 2026-08-22)

Six corrections to the plan above. Apply before executing.

### C1 — A2 must NOT delete the MemorySaver bullet
`README.md:137-143` makes two claims. "Does not survive a restart" is now false. **"Does not
work across multiple Cloud Run instances" is still true**: `DB_PATH` defaults to a relative
path on an ephemeral container filesystem and `SqliteSaver.from_conn_string` opens one file
per process. Deleting the bullet makes the README *less* honest. Rewrite it instead:
"SQLite checkpointing survives a local restart; it does not survive a Cloud Run instance
recycle and is not shared across instances. Next step is Postgres."

### C2 — A3 is not a binary choice; the validators are already wired
`validate_llm_config()` / `validate_search_config()` are called in three places:
`main.py:55-56` (lifespan), `providers.py:20`, `tools.py:21`. Restoring import-time
validation would be a regression: it breaks `--reload`, forces every test importing `app.*`
to carry env keys, and stops `/health` answering on a keyless box. **A3 is "rewrite
CLAUDE.md", full stop.** Keep the lifespan call so `/ready` fails loudly rather than a
config typo surfacing as a generic 500 at `main.py:166`.

### C3 — T8 opens an XSS sink and must ship with T5
`index.html:970-976` writes the error state via `innerHTML` with `${err.message}`
interpolated. T8 replaces that inert string with server text, and FastAPI's 422 body echoes
the caller's `input` verbatim. Build the error node with `textContent`. Do T5 and T8 in one
commit so the escaping decision is made once.

T8 also has three unhandled shapes: `detail` is a string for `HTTPException` but a
list-of-dicts for 422; slowapi's rate-limit handler returns `error`, not `detail`; and
`await response.json()` throws on a non-JSON error body (proxy 502, Cloud Run 503).

### C4 — T14 must default, and the topic needs a bound
`topic: str = ""`, not required: checkpoints predating the change would 500 on
`GET /research/{id}`. Add `max_length=500` to `ResearchRequest.topic` in the same edit; that
string is stored in a checkpoint and interpolated into three prompts. Keep `#reportHeadline`
on `textContent`.

### C5 — Test landmine before adding any test
`tests/test_api.py:133` sets `config.DB_PATH = db_file` and never restores it. Anything
appended after line 156 runs against a stale SQLite file instead of `MemorySaver`. Fix with
`monkeypatch.setattr` first. Then add exactly one test and two assertions:
the root-route test (the only mechanical guard against the `/home` defect), plus
`topic` assertions in `test_start_research_pauses_for_review` and `test_get_research_status`.
Drop the test count from the README rather than correcting it; it self-invalidates.

### C6 — B2 only reproduces locally
The pause/restart/resume recording is the headline differentiator, but `DB_PATH` is a
relative path into an ephemeral container FS and the Dockerfile mounts no volume. Label the
recording as local, or state in the README that a deployed instance loses paused threads on
recycle.

### Smaller, verified
- T3 is not one word: `<button>` drops `'Inter'` inheritance. Add `font-family: inherit` to
  `.topic-pill` **and** `.tab-btn`, which has the same latent bug.
- T7 has three spinner call sites (943, 991, 1016) plus one error site (970), not one.
- `escapeHtml` is used only in element-content positions, where it is adequate. The live bug
  is the *missing* call at line 1065, not a weak escaper. Do not harden `escapeHtml` and
  leave 1065 alone.
- `loadThread()` at line 999 interpolates `threadId` into a URL with no `encodeURIComponent`.
- A1's `index.html:711` instruction is dead: `fetchSystemStatus()` overwrites `#providerText`
  on load and never shows a model name.
- Plan contradiction: T5 is escalated because the README documents `--allow-unauthenticated`,
  while T6 is cut because "nothing is deployed." Both cannot be the operative fact.

### Open: T5 approach (voices disagree)
- **Vendor** `marked` + `dompurify`, pinned with version in the filename. Keeps rendered
  Markdown, removes the unversioned CDN at `index.html:12`, needs no Node toolchain.
- **`textContent`** removes the dependency but collapses newlines (no `white-space` rule on
  `.report-body`), makes both tabs render the same string, orphans ~30 lines of CSS, and
  **does not fix line 1065**. The prior plan's "~10 lines" costing was wrong.

Either way, line 1065 needs `createElement`/`setAttribute` with an `http:`/`https:` scheme
allowlist. That is not optional in either branch.

---

# EXECUTION PLAN

Ordered commit-by-commit. Each step lands independently, passes `ruff check .` and
`uv run pytest tests/ -v`, and is revertable on its own. Nothing here is started until
the T5 approach is chosen.

## Commit 1 — Test guard, before anything else

Land the guard first so the next commit has something proving it.

- Fix `tests/test_api.py:133`: `config.DB_PATH = db_file` leaks into every test appended
  after line 156. Change to `monkeypatch.setattr(config, "DB_PATH", db_file)`.
- Add the root-route test:

```python
def test_root_serves_the_web_ui(mocked_client):
    r = mocked_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
```

**Expected: this test FAILS.** That is the point. It proves the `/home` defect is real
before commit 2 fixes it.

Verify: `uv run pytest tests/test_api.py -v` shows 1 failure, 23 passes.

## Commit 2 — Restore the root route

- `main.py:120`: `@app.get("/home")` back to `@app.get("/")`. Keep `/home` as a second
  decorator if you want both.
- Zero other references repo-wide, so blast radius is nil.

Verify: the commit-1 test now passes. 24 tests green.

## Commit 3 — Repo truth pass (docs only, no code)

- `README.md:139`: rewrite the `MemorySaver` bullet, do not delete it. Half is still
  true. Replacement: "SQLite checkpointing survives a local restart. It does not survive
  a Cloud Run instance recycle and is not shared across instances, because `DB_PATH` is a
  relative path on an ephemeral container filesystem. Next step is Postgres."
- `README.md:104,184`: drop the hardcoded test count. It self-invalidates on commit 1.
  Say "fully mocked, no real API calls."
- `CLAUDE.md` config section: it claims import-time `RuntimeError`. Validation now lives
  in `validate_llm_config()` / `validate_search_config()`, called from `main.py:55-56`
  (lifespan), `providers.py:20`, and `tools.py:21`. Rewrite to describe startup-time
  validation. Do not restore import-time; that breaks `--reload` and forces env keys on
  every test import.
- Decide `package.json` / `package-lock.json`: both are stubs in a uv-managed Python
  repo. Delete or explain.
- Decide the `GROQ_CHAT_MODEL` default (`config.py:46` is now `openai/gpt-oss-120b`).
  If intended, update `.env.example` and the README. Skip `index.html:711`: that string
  is overwritten by `fetchSystemStatus()` on load and never displays a model name.

Verify: `grep -n MemorySaver README.md` shows the rewritten bullet. No test change.

## Commit 4 — T5 + T8 together

These ship as one commit so the escaping decision is made once. T8 alone would open a
sink: `index.html:970` writes errors via `innerHTML` with `${err.message}` interpolated,
and T8's whole purpose is to put server text there.

**T5, whichever approach is chosen at the gate.** In both branches, line 1065 gets the
same treatment and it is not optional:

```js
const a = document.createElement('a');
const u = new URL(s.url, location.origin);
if (!['http:', 'https:'].includes(u.protocol)) return;   // reject javascript:, data:
a.href = u.href;
a.textContent = u.href;
```

**T8** with defensive parsing. Three shapes to handle:
- `detail` is a string for `HTTPException`, a list-of-dicts for 422
- slowapi's rate-limit handler returns `error`, not `detail`
- a non-JSON error body (proxy 502, Cloud Run 503) makes `await response.json()` throw

```js
let msg;
try {
  const d = await r.json();
  msg = typeof d.detail === 'string' ? d.detail
      : (d.error || (Array.isArray(d.detail) ? d.detail.map(e => e.msg).join('; ') : null))
        || `Request failed (${r.status})`;
} catch { msg = `HTTP ${r.status}`; }
```

Then build the error node with `textContent`, not a template literal.

Also in this commit, two characters: `encodeURIComponent(threadId)` at line 999.

Verify by hand: paste `<img src=x onerror=alert(1)>` as a topic, confirm inert. Trigger a
422 (empty topic), confirm the message renders as text. Trigger a 429 (11 rapid launches),
confirm it does not print "undefined".

## Commit 5 — T3, T4, T7

- **T3**: `<div class="topic-pill" onclick>` at 740-743 becomes
  `<button type="button" class="topic-pill">`. Add `font-family: inherit` to
  `.topic-pill` **and** `.tab-btn`, which has the same latent bug. Without it the
  buttons drop `'Inter'` and change typeface.
- **T4**: `.review-textarea:focus` gets a visible indicator. Match the existing pattern
  at line 201 (border colour plus glow) rather than reinstating a browser outline, or the
  two focus states will not match.
- **T7**: `switchTab('rendered')` at the top of `startResearch`, `loadThread`, and
  `submitReview`. Three call sites, not one. Better: one `showInReport()` helper.

Verify by hand: Tab reaches all four pills, Enter and Space activate them, the textarea
shows focus, and launching from the Raw tab shows the spinner.

## Commit 6 — T14

- `main.py:96-105`: add `topic: str = ""` to `ResearchResponse`. **Defaulted, not
  required** — a checkpoint predating this change would 500 on `GET /research/{id}`.
- `_state_to_response`: `topic=state.get("topic", "")`.
- `main.py:88`: add `max_length=500` to `ResearchRequest.topic`. That string is stored in
  a checkpoint and interpolated into three prompts.
- `index.html:1043`: headline the topic, demote thread ID and status to metadata. Keep
  the line on `textContent`.
- Two assertions, no new test function: `assert body["topic"] == "..."` in
  `test_start_research_pauses_for_review` and `test_get_research_status`. The second
  covers the `loadThread()` recovery path this task exists to serve.

Verify: `uv run pytest tests/ -v` green.

## Phase B — after the commits land

Manual, no code. B1: screenshot or GIF of the Web Studio in the README with the URL.
B2: recorded pause, kill server, restart, resume, finalize. **Label the recording local.**
`DB_PATH` is a relative path into an ephemeral container FS and the Dockerfile mounts no
volume, so a deployed instance will not reproduce it.

## Verification summary

| Step | Automated | Manual |
|---|---|---|
| 1 | pytest: 1 expected failure | — |
| 2 | pytest: 24 green | load `/` |
| 3 | none | read the diff |
| 4 | none | XSS paste, 422, 429 |
| 5 | none | keyboard tab, Raw-tab launch |
| 6 | pytest: 24 green + 2 assertions | headline shows topic |

**Five of the six code tasks ship with zero automated verification.** That is acceptable
at this size, but it should be said out loud rather than discovered later. Do not add
jsdom or Playwright for four DOM tweaks in a single-file UI.

---

## T5 RESOLVED: vendor + pin

Approved at the /autoplan final gate. Commit 4 uses:
- `app/static/vendor/marked-<version>.min.js` and `dompurify-<version>.min.js`, version in
  the filename, source URL in a comment above the `<script>` tags.
- `DOMPurify.sanitize(marked.parse(content))` replacing `index.html:1046`.
- Source anchors rebuilt with `createElement` / `textContent` / `setAttribute` and an
  `http:` / `https:` scheme allowlist. Required in either branch; not optional.
- Remove the three CDN `<script>` / `<link>` tags at `index.html:8-12`.

Rejected: dropping `marked` for `textContent`. It collapses newlines (no `white-space`
rule on `.report-body`), makes both tabs render the same string, orphans ~30 lines of CSS,
and leaves line 1065 unfixed.

**No open decisions remain. The plan is executable.**

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 6 | 3 | T5 = vendor + pin | Taste (voices split) | P1, P5 | Keeps Markdown the writer prompt emits; removes the unversioned jsdelivr load; textContent's cost was mis-stated | textContent |
| 7 | 3 | T5 and T8 ship in one commit | Mechanical | P5 | T8 alone opens an innerHTML sink at line 970; escaping decided once | Separate commits |
| 8 | 3 | Test guard lands before the route fix | Mechanical | P6 | The revert commit needs a failing test proving the defect | Fix first, test after |
