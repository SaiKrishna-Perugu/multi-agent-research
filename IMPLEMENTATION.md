# Implementation Guide

Standalone. You do not need to read `PLAN.md` to work from this file.

Six commits plus two manual artifacts. Every step has exact line numbers, the code to
write, a verification step, and a commit message. Do them in order: step 1 exists to make
step 2 provable, and step 4 must be one commit or it opens a security hole.

Line numbers are from the working tree as of 2026-08-22. If you edit above a referenced
line, the numbers below it shift.

**Total: ~4 hours by hand.**

---

## Before you start

Run this and keep the output. You will compare against it at the end.

```bash
uv run ruff check .
uv run pytest tests/ -v          # expect 23 passed
git status --short
```

### Three decisions only you can make

The working tree has 16 days of uncommitted drift. Decide these before writing code,
because steps 2 and 3 depend on the answers.

| # | What changed | Your options |
|---|---|---|
| D1 | `app/main.py:120` is `@app.get("/home")`, not `@app.get("/")`. **The UI root 404s.** | Restore `/`, or keep `/home` and update every doc that says otherwise. This guide assumes restore. |
| D2 | `app/config.py:46` — `GROQ_CHAT_MODEL` default is now `openai/gpt-oss-120b`, was `llama-3.3-70b-versatile`. | Intended, or an experiment you forgot? If intended, `.env.example` and the README need updating. |
| D3 | `package.json` and `package-lock.json` are untracked stubs in a uv-managed Python repo. | Delete them, or add a line to the README explaining what they are for. |

---

## Step 1 — Test guard

**Land this first.** The new test is *supposed to fail*. That failure is your evidence the
`/home` defect is real, and it gives step 2 something to prove.

### 1a. Fix the DB_PATH leak

`tests/test_api.py:133` assigns a module global and never restores it. Every test added
after line 156 would silently run against a stale SQLite file instead of `MemorySaver`.

**Find (line 124):**
```python
def test_sqlite_persistence_across_app_restarts(tmp_path, fake_agents):
```
**Change the signature to add `monkeypatch`:**
```python
def test_sqlite_persistence_across_app_restarts(tmp_path, fake_agents, monkeypatch):
```

**Find (line 133):**
```python
    config.DB_PATH = db_file
```
**Replace with:**
```python
    monkeypatch.setattr(config, "DB_PATH", db_file)
```

### 1b. Add the root-route test

Append to the end of `tests/test_api.py`:

```python
def test_root_serves_the_web_ui(mocked_client):
    r = mocked_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
```

### Verify

```bash
uv run pytest tests/ -v
```
**Expect: 23 passed, 1 failed.** The failure is `test_root_serves_the_web_ui` returning
404. If it passes, someone already fixed the route and you can skip step 2.

```
git add tests/test_api.py
git commit -m "test: add root-route guard and stop DB_PATH leaking between tests"
```

---

## Step 2 — Restore the root route

**Find `app/main.py:120`:**
```python
@app.get("/home", include_in_schema=False)
def root():
```
**Replace with:**
```python
@app.get("/", include_in_schema=False)
def root():
```

Nothing else in the repo references either path. The UI fetches only `/research`,
`/research/{id}`, `/research/{id}/review`, and `/ready`, all absolute, so it works from
any mount point. The Dockerfile has no `HEALTHCHECK`. Blast radius is nil.

If you want both paths, stack the decorators:
```python
@app.get("/", include_in_schema=False)
@app.get("/home", include_in_schema=False)
def root():
```

### Verify

```bash
uv run pytest tests/ -v          # expect 24 passed, 0 failed
uv run uvicorn app.main:app --reload
# open http://127.0.0.1:8000/ — the Web Studio should load
```

```
git add app/main.py
git commit -m "fix: restore GET / so the Web Studio is reachable again"
```

---

## Step 3 — Repo truth pass

Docs only. No code, no tests. This is the highest value-per-minute step in the guide: it
is what a reviewer reads first.

### 3a. Rewrite the MemorySaver bullet — do not delete it

`README.md:139-144` currently claims `MemorySaver`. The code uses `SqliteSaver`
(`main.py:61`). **But half the bullet is still true**, so deleting it makes the README
less honest, not more.

**Find:**
```markdown
- **`MemorySaver` is in-process and per-instance.** A paused (awaiting-review)
  thread does not survive a restart, and does not work correctly if Cloud
  Run scales to more than one instance -- each instance has its own memory,
  so a review call could land on an instance that never saw the original
  request. Fine for a single-instance demo; the documented next step is a
  persisted checkpointer (LangGraph supports Postgres/SQLite backends).
```
**Replace with:**
```markdown
- **SQLite checkpointing is per-instance, not shared.** A paused
  (awaiting-review) thread survives a local process restart, which is the
  point of the SQLite checkpointer. It does not survive a Cloud Run instance
  recycle and is not shared across instances: `DB_PATH` defaults to a
  relative path on an ephemeral container filesystem, so each instance gets
  its own file and that file dies with the instance. Fine for a
  single-instance demo; the documented next step is Postgres.
```

### 3b. Drop the hardcoded test counts

They are wrong now (23, not 20) and will be wrong again after step 6 (25). Stop
maintaining a number.

**`README.md:104`** — change `20 tests, all fully mocked (no real API calls) -- covers`
to `Fully mocked, no real API calls -- covers`.

**`README.md:184`** — change `tests/                        # 20 tests, fully mocked`
to `tests/                        # fully mocked, no real API calls`.

### 3c. Fix the CLAUDE.md config section

`CLAUDE.md` says config raises `RuntimeError` at import time and that this is why config
errors surface as import failures. **No longer true.** Validation now lives in
`validate_llm_config()` and `validate_search_config()` (`config.py:90,100`), called from
`main.py:55-56` in the lifespan, plus `providers.py:20` and `tools.py:21`.

Replace the `app/config.py` bullet with:

```markdown
- **`app/config.py`** — every module reads settings from here, never `os.getenv` directly.
  Required keys are validated by `validate_llm_config()` / `validate_search_config()`,
  called during FastAPI's **lifespan startup** (`main.py:55-56`) and again inside
  `get_llm()` and `_get_search_tool()`. Import stays side-effect-free, so `--reload` works
  on a keyless box and tests can import `app.*` without env keys. A misconfigured
  deployment fails at startup, not on the first request.
```

Do **not** restore import-time validation. It breaks `uvicorn --reload`, stops `/health`
answering on a keyless box, and forces env keys on every test import (which is why CI
injects placeholders).

### 3d. Apply your D2 and D3 answers

If `openai/gpt-oss-120b` is the intended default, update `.env.example` and the README.

Skip `index.html:711` even though it hardcodes `"Provider: Groq (LLaMA-3.3-70B)"`.
`fetchSystemStatus()` at line 1092 overwrites `#providerText` on load and never shows a
model name at all, so editing that string changes nothing visible. Either add the model to
`/ready`'s payload (`main.py:138`) and render it, or leave the badge alone.

### Verify

```bash
grep -n "MemorySaver\|20 tests" README.md    # should return nothing
uv run pytest tests/ -v                      # still 24 passed
```

```
git add README.md CLAUDE.md .env.example
git commit -m "docs: correct stale MemorySaver and config-validation claims"
```

---

## Step 4 — Security: sanitize rendering and error text

**This must be one commit.** Doing the error-message work without the escaping work opens
a new injection path, because `index.html:970` writes errors via `innerHTML`.

### 4a. Vendor the dependencies

Pick a specific version of each from npm, then:

```bash
mkdir -p app/static/vendor
# substitute the versions you chose
curl -o app/static/vendor/marked-16.4.1.min.js \
  https://cdn.jsdelivr.net/npm/marked@16.4.1/marked.min.js
curl -o app/static/vendor/dompurify-3.2.7.min.js \
  https://cdn.jsdelivr.net/npm/dompurify@3.2.7/dist/purify.min.js
```

Put the version in the filename and the source URL in a comment. That is your update path.

**Find `index.html:11-12`:**
```html
    <!-- Marked.js for Markdown Rendering -->
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
```
**Replace with:**
```html
    <!-- Vendored, pinned. Source: https://cdn.jsdelivr.net/npm/marked@16.4.1/marked.min.js -->
    <script src="/static/vendor/marked-16.4.1.min.js"></script>
    <!-- Vendored, pinned. Source: https://cdn.jsdelivr.net/npm/dompurify@3.2.7/dist/purify.min.js -->
    <script src="/static/vendor/dompurify-3.2.7.min.js"></script>
```

The old URL was **unversioned**, so jsdelivr served whatever `latest` resolved to at page
load, with full page authority, on a UI the README tells you to deploy
`--allow-unauthenticated`. That is the supply-chain reason this matters, separate from
the XSS.

### 4b. Sanitize the report body

**Find `index.html:1046`:**
```js
            document.getElementById('reportDisplay').innerHTML = `<div class="report-body">${marked.parse(content)}</div>`;
```
**Replace with:**
```js
            const body = document.createElement('div');
            body.className = 'report-body';
            body.innerHTML = DOMPurify.sanitize(marked.parse(content));
            const disp = document.getElementById('reportDisplay');
            disp.replaceChildren(body);
```

Keep the `data.final_report || data.draft || 'No content available.'` fallback on line
1042. `marked.parse(undefined)` throws.

### 4c. Rebuild the source list with DOM APIs

This is the bug that survives every other fix. `href="${s.url}"` is interpolated with no
escaping and no scheme check, and `s.url` comes from Tavily, i.e. a scraped third-party
page.

**Find `index.html:1062-1067`:**
```js
                sourcesContainer.innerHTML = data.sources.map(s => `
                    <div class="source-item">
                        <div class="source-title">${escapeHtml(s.title || 'Web Reference')}</div>
                        <a class="source-url" href="${s.url}" target="_blank" rel="noopener">${escapeHtml(s.url)}</a>
                    </div>
                `).join('');
```
**Replace with:**
```js
                sourcesContainer.replaceChildren();
                for (const s of data.sources) {
                    if (!s || !s.url) continue;
                    let u;
                    try { u = new URL(s.url); } catch { continue; }
                    if (u.protocol !== 'http:' && u.protocol !== 'https:') continue;

                    const item = document.createElement('div');
                    item.className = 'source-item';

                    const title = document.createElement('div');
                    title.className = 'source-title';
                    title.textContent = s.title || 'Web Reference';

                    const link = document.createElement('a');
                    link.className = 'source-url';
                    link.href = u.href;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    link.textContent = u.href;

                    item.append(title, link);
                    sourcesContainer.append(item);
                }
```

The `if (!s || !s.url) continue;` guard matters: `sources` is typed `list` with no shape
guarantee, and a `null` element used to throw mid-render, leaving the report visible with
the review controls hidden and no error shown.

### 4d. Error messages, parsed defensively

Add this helper next to `escapeHtml`:

```js
        async function errorMessage(response) {
            try {
                const d = await response.json();
                if (typeof d.detail === 'string') return d.detail;
                if (Array.isArray(d.detail)) return d.detail.map(e => e.msg).join('; ');
                if (d.error) return d.error;
            } catch { /* non-JSON body */ }
            return `Request failed (${response.status})`;
        }
```

Three shapes it handles, all reachable today:
- `detail` is a **string** for `HTTPException`
- `detail` is a **list of dicts** for 422 (empty topic hits this)
- the rate limiter returns **`error`**, not `detail` (11 launches in a minute hits this)
- a non-JSON body (proxy 502, Cloud Run 503) would otherwise throw *inside* your error handler

Then replace the three throw sites:

**Line 961:** `throw new Error(\`HTTP error! status: ${response.status}\`);`
**Line 1001:** `throw new Error(\`Thread not found or error status: ${response.status}\`);`
**Line 1031:** `throw new Error(\`Review submit failed: ${response.status}\`);`

All three become:
```js
                    throw new Error(await errorMessage(response));
```

### 4e. Stop the error state being an injection sink

**Find `index.html:970-976`:**
```js
                document.getElementById('reportDisplay').innerHTML = `
                    <div class="empty-state" style="color: var(--accent-error);">
                        <div class="empty-state-icon">⚠️</div>
                        <h3>Research Request Failed</h3>
                        <p>${err.message}</p>
                    </div>
                `;
```
**Replace with:**
```js
                const box = document.createElement('div');
                box.className = 'empty-state';
                box.style.color = 'var(--accent-error)';
                const icon = document.createElement('div');
                icon.className = 'empty-state-icon';
                icon.textContent = '⚠️';
                const h = document.createElement('h3');
                h.textContent = 'Research Request Failed';
                const p = document.createElement('p');
                p.textContent = err.message;
                box.append(icon, h, p);
                document.getElementById('reportDisplay').replaceChildren(box);
```

Without this, step 4d would make things worse: FastAPI's 422 body echoes the caller's own
input verbatim, so the path becomes topic field → `detail[0].input` → `err.message` →
`innerHTML`.

### 4f. Two characters

**Line 999:** `fetch(\`/research/${threadId}\`)` becomes
`fetch(\`/research/${encodeURIComponent(threadId)}\`)`.

### Verify (manual — no test covers this)

Start the server, then:

1. Enter `<img src=x onerror=alert(1)>` as a topic. It must render as visible text, no dialog.
2. Submit an empty topic. You should see a readable sentence, not `[object Object]` and not `HTTP error! status: 422`.
3. Click Launch 11 times inside a minute. The 11th must show the rate-limit sentence, not `undefined`.
4. Load a report and confirm sources still render as clickable links.

```
git add app/static/vendor app/static/index.html
git commit -m "fix: sanitize rendered markdown, validate source URLs, vendor JS deps"
```

---

## Step 5 — Keyboard access and the hidden-spinner bug

### 5a. Pills become real buttons

**Find `index.html:740-743`** — four lines of `<div class="topic-pill" onclick="...">`.

Change each `<div` to `<button type="button"` and each closing `</div>` to `</button>`.
Leave the `onclick` and the text alone.

**Then fix the font.** A `<button>` does not inherit `body`'s `'Inter'`, so without this
all four pills change typeface.

`.topic-pill` (line 279) — add:
```css
            font-family: inherit;
```

`.tab-btn` (line 481) — **same bug, already shipped.** Add the same line:
```css
            font-family: inherit;
```

### 5b. Focus ring on the review textarea

`.review-textarea` sets `outline: none` (line 433) with no `:focus` rule anywhere. A
keyboard user has zero indication of position immediately before two irreversible buttons.

Add after the `.review-textarea` block (after line 435), matching the existing pattern at
line 201 rather than reinstating a browser outline:

```css
        .review-textarea:focus {
            border-color: var(--accent-warning);
            box-shadow: 0 0 15px rgba(245, 158, 11, 0.25);
        }
```

### 5c. Force the rendered tab before writing to it

`switchTab('raw')` sets `reportDisplay.style.display = 'none'`, and every spinner and
error writes into `#reportDisplay`. Launch from the Raw tab and the button looks dead for
40 seconds.

Add `switchTab('rendered');` as the first line inside **three** functions:

- `startResearch()` — after the empty-topic guard, before `isProcessing = true` (~line 938)
- `loadThread()` — after the empty-ID guard (~line 990)
- `submitReview()` — after the `currentThreadId` guard (~line 1012)

Three call sites, not one. That is the part that is easy to get wrong.

### Verify (manual)

1. Tab through the page. All four example pills must receive focus, and Enter and Space must both activate them.
2. Tab into the feedback textarea. The border must change colour.
3. Confirm the pills and tabs still render in Inter, not a serif or system font.
4. Click Raw Markdown, then Launch. The spinner must appear.

```
git add app/static/index.html
git commit -m "fix: keyboard-accessible topic pills, textarea focus ring, tab reset on load"
```

---

## Step 6 — Return the topic

The report headline currently reads `Report: 3f4a1b2c... (finalized)`. The topic you typed
never appears, and `loadThread()` cannot recover it because the API does not return it.

### 6a. Response model

**`app/main.py:96-104`**, add one field to `ResearchResponse`:
```python
    thread_id: str
    status: str
    topic: str = ""
    draft: str = ""
```

**Defaulted, not required.** A checkpoint created before this change has no `topic`, and a
required field would turn `GET /research/{id}` into a 500 for those threads.

**`_state_to_response`** (line 107), add one line:
```python
        status=state.get("status", "unknown"),
        topic=state.get("topic", ""),
        draft=state.get("draft", ""),
```

### 6b. Bound the input

**`app/main.py:88`:**
```python
    topic: str = Field(..., min_length=1, max_length=500, examples=["The current state of small modular nuclear reactors"])
```

That string is stored in a checkpoint and interpolated into three LLM prompts. It needs a ceiling.

### 6c. Headline the topic

**Find `index.html:1043`:**
```js
            document.getElementById('reportHeadline').textContent = `Report: ${data.thread_id.substring(0, 8)}... (${data.status})`;
```
**Replace with:**
```js
            document.getElementById('reportHeadline').textContent =
                data.topic || `Report: ${data.thread_id.substring(0, 8)}... (${data.status})`;
```

Keep it on `textContent`. Switching to `innerHTML` to style the topic would add a
user-controlled sink and undo step 4.

### 6d. Two assertions, no new test

In `tests/test_api.py`, add one line to each of two existing tests:

`test_start_research_pauses_for_review` (line 17):
```python
    assert body["topic"] == "small modular reactors"
```

`test_get_research_status` (line 33) — this one covers the `loadThread()` recovery path
the whole task exists for:
```python
    assert r.json()["topic"] == "test"
```

### Verify

```bash
uv run pytest tests/ -v          # expect 24 passed
uv run ruff check .
```
Then start a report in the browser and confirm the headline shows your topic.

```
git add app/main.py app/static/index.html tests/test_api.py
git commit -m "feat: return topic on ResearchResponse and headline reports with it"
```

---

## Step 7 — Make the differentiated claim visible

No code. This is the part that changes what a reviewer believes, and it is currently
missing entirely: the README's Usage section is curl only, with no screenshot and no URL.

### 7a. Screenshot in the README

Run the app, generate one report, screenshot the Web Studio mid-review with the draft and
the approve/revise banner visible. Add it near the top of the README with one line telling
the reader the URL:

```markdown
The Web Studio at `http://127.0.0.1:8000/` — launch a report, watch the agent
pipeline, and approve or revise the draft.

![Web Studio](docs/web-studio.png)
```

### 7b. Record the pause, restart, resume

This is the rarest thing your repo does and nothing currently lets anyone see it.

```bash
# 1. start a report, note the thread_id, let it pause for review
# 2. Ctrl+C the server. Fully stop it.
# 3. restart:  uv run uvicorn app.main:app
# 4. GET /research/{thread_id} — the draft is still there, from SQLite
# 5. approve it, get the final report
```

Record it as a terminal cast or GIF. **Step 2 is the whole point.** Every multi-agent demo
on GitHub can pause; almost none survive a process restart.

**Label the recording local.** `DB_PATH` is a relative path on an ephemeral container
filesystem and the Dockerfile mounts no volume, so a Cloud Run instance will not reproduce
this. Say so in the caption, or the demo becomes a claim you cannot back.

```
git add README.md docs/
git commit -m "docs: add Web Studio screenshot and pause-restart-resume recording"
```

---

## Final check

```bash
uv run ruff check .
uv run pytest tests/ -v          # 24 passed
git log --oneline -7
```

You should have seven commits, each independently revertable.

### What ships without automated coverage

Steps 4 and 5 are entirely manual to verify. That is a deliberate call at this size — do
not add Playwright or jsdom for four DOM tweaks in a single-file UI — but know it:

| Change | Covered by a test? |
|---|---|
| Root route restored | Yes, step 1b |
| `topic` on the response | Yes, step 6d |
| Markdown sanitization | **No** — manual XSS paste |
| Source URL scheme check | **No** — manual |
| Error message parsing | **No** — manual 422 and 429 |
| Keyboard-accessible pills | **No** — manual tab-through |
| Textarea focus ring | **No** — manual |
| Tab reset before loading | **No** — manual |

### Rolling back

Each step is one commit. `git revert <sha>` undoes any single step. The only ordering
constraint is that step 2 assumes step 1's test exists, and step 4's sub-parts must stay
together.

---

## Not doing (and why)

Fourteen tasks from the original design review were cut. They are real defects, but they
do not move what this repo is for. Full list and reasoning is in `PLAN.md`.

The short version: mobile reflow, an API-key field, a live progress tracker, SVG icons,
design tokens, and typography work. The mobile work was measured to the pixel for a 375px
viewport that will not load a JD-targeted portfolio repo. The progress tracker needs an
async backend change that breaks on Cloud Run, where CPU is throttled outside request
handling and instances scale to zero.

If priorities change, every cut task is still in
`~/.gstack/projects/SaiKrishna-Perugu-multi-agent-research/tasks-design-review-20260822-161323.jsonl`.
