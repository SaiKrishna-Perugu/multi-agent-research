# gstack setup on Windows

The nested Git project has 55 Codex skills installed under `.agents/skills/`.
The runtime is gstack 1.91.1.0, revision
`2a113ae7e623f590095bcaaa0cc581c9a10a6632`, from the existing Claude installation.
Its source, compiled Windows tools, dependencies, and state are local to
`.gstack/`; generated skills and runtime data are gitignored.

The PowerShell launcher supplies Git Bash utilities. Shell helpers use relative
state paths from the actual project root, avoiding Git Bash's attempts to create
protected ancestor directories. On Windows, the browser runs with Node.js and
the installed Playwright Chromium cache. The startup allowance is 45 seconds;
`BROWSE_START_TIMEOUT` can override it.

## Use from the Git project

Open this directory, containing `pyproject.toml` and `.git`, as the Codex workspace.
Skills become available on the next turn. Examples: `$gstack`, `$gstack-review`,
`$gstack-investigate`, `$gstack-browse`, and `$gstack-qa`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\gstack.ps1 doctor
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\gstack.ps1 browse status
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\gstack.ps1 run gstack-config get skill_prefix
```

Git Bash commands should first source `scripts/gstack-env.sh` in each new shell.
The generated skill preambles do this automatically.

## Finish discovery in the outer workspace

This Codex session cannot write to the outer workspace's protected `.agents/skills`
directory. Registration there is pending.

An attempted runtime relocation followed a dependency junction and moved 1,629
dependency files out of the existing Claude installation. The files are preserved
in `.gstack/source/node_modules/`. Its `bun.lock` matches the original installation.
The restore script copies only missing files, preserves existing files, verifies
each restored file's SHA256, and then registers the outer workspace skills.
Restoration remains pending because this session cannot write to the original
home-directory installation.

Run this from the outer workspace in a normal PowerShell window:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\multi-agent-research\scripts\repair-gstack.ps1 -RegisterWorkspace
```

Then start a new Codex turn so the restoration and registration can be verified.
To preview the missing dependency count without writes, run the repair script
with `-CheckOnly`. To repeat just skill registration, run `gstack.ps1 setup
-Workspace`. This registers the same generated skills at both workspace levels.
Running `setup` without `-Workspace` refreshes the Git project's
registration only. `setup -StageOnly` prepares files under `.gstack/skills/`
without changing skill discovery locations. Setup preserves unrelated skills.

The launcher refreshes this existing runtime's Codex registrations; it does not
download an updated gstack runtime. For upstream installation and supported host
requirements, see [gstack](https://github.com/garrytan/gstack).

## Verification

Verified: skill generation, unique Codex skill names, shell helper configuration,
the skill-start protocol, browser navigation to a local HTTP page, clicking a
button and checking the resulting text, and a saved screenshot with no browser
console errors. Setup evidence is stored under `.gstack/`.

These checks verify the installation and browser engine. Individual workflows
can require additional tools or accounts; for example, outside reviews need an
authenticated Claude Code CLI, and iOS workflows need a Mac and device.
