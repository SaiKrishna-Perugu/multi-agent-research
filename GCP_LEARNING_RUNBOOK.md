# Owner-run private Cloud Run learning deployment

This is a plan for YOU to run after the P1 fixes and tests in PROJECT_READINESS_PLAN.md.
No command here has been run against your cloud account. Use a separate learning service;
never substitute an existing production service without reviewing its policies and data.

## 0. Choose and record the experiment

- One user, disposable reports, one accepted background run at a time after R05.
- Monthly learning budget: ______. End date: ______. Set budget alerts in Billing.
  Alerts do not stop spending. Cloud Run idle CPU/min instances, builds, images, logs and
  storage may cost money even when no report is generated.
- Groq first: external Groq + Tavily keys; fewer Vertex setup steps, provider quota still applies.
- Vertex alternative: GCP Vertex access and an available model; Tavily key still required.
  Do not assume the repository's default model name is available to your project.
- Keep TypeSafe and LangSmith off until their correctness/privacy/latency gates pass.
- Shared PostgreSQL and durable jobs are deferred. Export reports before every redeploy.

Read each command block, replace placeholders, run one stage and verify before continuing.
In your MAIN operator terminal, first define this checked native-command helper:
~~~powershell
function Invoke-Checked {
    $nativeCommand = $args[0]
    $nativeArguments = @($args | Select-Object -Skip 1)
    & $nativeCommand @nativeArguments
    if ($LASTEXITCODE -ne 0) { throw "$nativeCommand failed. Stop and resolve the error." }
}
~~~
The blocks below use it to stop immediately on native-command failure. Do not strip the wrapper.
It does not print arguments or secret values. Use the same main terminal for stages 1–7 and 9–10;
stage 8 includes a self-contained second-terminal sequence.

## 1. Work from the application repository and pass quality gates

~~~powershell
# Verify/install Git, uv (Python 3.13), Google Cloud CLI and Docker first.
# Docker Desktop must be running; Linux CI is the documented alternative.
Set-Location 'C:\Users\psaik\Downloads\multi-agent-research\multi-agent-research\multi-agent-research' -ErrorAction Stop
if (-not (Test-Path ./Dockerfile) -or -not (Test-Path ./uv.lock) -or -not (Test-Path ./app/main.py)) { throw 'Not the application repository' }
Invoke-Checked git --version
Invoke-Checked uv --version
Invoke-Checked gcloud version
Invoke-Checked docker version
Invoke-Checked git status --short
Invoke-Checked uv sync --frozen
Invoke-Checked uv run ruff check .
Invoke-Checked uv run ruff format --check .
~~~

Start with a dedicated MAIN operator shell and continue in that SAME shell so Invoke-Checked
remains defined. Set placeholder credentials below before importing app modules. They affect
this shell only; real runtime secrets are separately injected from Secret Manager.
~~~powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:MODEL_PROVIDER = 'groq'
$env:GROQ_API_KEY = 'gsk-test-placeholder-not-real'
$env:TAVILY_API_KEY = 'tvly-test-placeholder-not-real'
$env:API_KEY = ''
$env:GCP_PROJECT_ID = ''
$env:ENABLE_TYPESAFE = 'false'
$env:TYPESAFE_API_KEY = ''
$env:LANGSMITH_TRACING = 'false'
$env:LANGCHAIN_TRACING_V2 = 'false'
Invoke-Checked uv run --frozen python -m pytest tests/ -v --cov=app --cov-report=term-missing
Invoke-Checked powershell -NoProfile -File ./tests/test_deploy_vertex.ps1
Invoke-Checked docker build -t multi-agent-research:local .
~~~

Expected: lint/format, full tests, rollout tests and image build pass. No real model calls.
If Windows blocks a native DLL, use the Linux CI/container route; do not disable Application
Control. If local Docker cannot run, a Linux CI pass at the exact commit plus a Cloud Build
image boot verification is an alternative. Never waive the full test gate.

For the Windows native-DLL block observed in this review, keep lint/format and PowerShell
checks, then replace only the failed host pytest command with this Linux container route.
It mounts app/tests read-only and does not mount .env or the local database:
~~~powershell
Invoke-Checked docker build --target builder -t research-learning-tests:local .
$ReviewApp = (Resolve-Path ./app).Path
$ReviewTests = (Resolve-Path ./tests).Path
Invoke-Checked docker run --rm --mount "type=bind,source=$ReviewApp,target=/app/app,readonly" --mount "type=bind,source=$ReviewTests,target=/app/tests,readonly" -e MODEL_PROVIDER=groq -e GROQ_API_KEY=gsk-test-placeholder-not-real -e TAVILY_API_KEY=tvly-test-placeholder-not-real -e API_KEY= -e GCP_PROJECT_ID= -e ENABLE_TYPESAFE=false -e TYPESAFE_API_KEY= -e LANGSMITH_TRACING=false -e LANGCHAIN_TRACING_V2=false -e PYTHON_DOTENV_DISABLED=1 research-learning-tests:local /bin/sh -c "uv sync --frozen && uv run python -m pytest tests/ -q -o cache_dir=/tmp/pytest-cache"
~~~
The container's && stops its test command if dependency sync fails. Expect the full suite to pass;
continue with rollout tests and production image build afterward. This is Linux verification,
not a Windows security-policy workaround or proof of live provider access.

Do not deploy an unreviewed dirty tree; record the exact approved commit/build source.

## 2. Authenticate and select explicit resource names

~~~powershell
Invoke-Checked gcloud auth login
Invoke-Checked gcloud auth list
$ProjectId = Read-Host 'Your learning project ID (not project number)'
$OwnerEmail = Read-Host 'The active Google account email'
$Region = 'us-central1' # change deliberately; keep repository/build/service region aligned
$Service = 'multi-agent-research-learning'
$Repo = 'research-learning'
$RuntimeName = 'research-learning-runtime'
$BuildName = 'research-learning-build'
$RuntimeSa = "$RuntimeName@$ProjectId.iam.gserviceaccount.com"
$BuildSa = "$BuildName@$ProjectId.iam.gserviceaccount.com"
$Bucket = "$ProjectId-research-learning-build"
$Tag = "review-$(Get-Date -Format yyyyMMdd-HHmmss)-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$Image = "$Region-docker.pkg.dev/$ProjectId/$Repo/multi-agent-research:$Tag"
Invoke-Checked gcloud projects describe $ProjectId --format='value(projectId)'
Invoke-Checked gcloud config set project $ProjectId
~~~

Expected: correct active account and project. In Cloud Console create a dedicated project if
needed, link YOUR intended billing account, verify billing enabled and organization policies.
Project creation/billing linkage need the appropriate project/billing administrator; no account
ID is assumed. Choose a globally unique bucket name if the example is taken.

Before bootstrap, confirm that this learning service/resource naming is unused or intentionally
owned by you. Existing-name/conflict errors are a stop-and-inspect condition, not a reason to
delete resources or suppress all errors.

## 3. Separate identities and enable APIs

A project administrator performs bootstrap. Routine deploys should not need broad owner access.

| Identity | Required access / scope |
|---|---|
| Bootstrap administrator | Enable APIs, create service accounts/repository/bucket/secrets and grant IAM. Billing linkage handled by billing admin. |
| Human operator | Cloud Build Editor and Service Usage Consumer in project; Cloud Run Admin for creation/configuration; Service Account User on build/runtime accounts; source bucket object access, repository read, logs read. |
| Build account | Artifact Registry Writer on learning repo; Storage Object Admin + bucket metadata read on dedicated build bucket; project Logging Writer for the default dual-log mode. No provider keys or runtime Vertex access. |
| Runtime account | Secret Accessor on selected individual secrets; Vertex AI User only for Vertex path. No build/deploy/admin roles. |
| Browser/proxy owner | Cloud Run Invoker on only the learning service. |
| Google-managed service agents | Retain service-specific default grants. Never repurpose these identities as the runtime account. |

~~~powershell
Invoke-Checked gcloud services enable iam.googleapis.com cloudresourcemanager.googleapis.com run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com storage.googleapis.com --project=$ProjectId
Invoke-Checked gcloud iam service-accounts create $RuntimeName --display-name='Research learning runtime' --project=$ProjectId
Invoke-Checked gcloud iam service-accounts create $BuildName --display-name='Research learning build' --project=$ProjectId
Invoke-Checked gcloud artifacts repositories create $Repo --repository-format=docker --location=$Region --description='Disposable research learning images' --project=$ProjectId
Invoke-Checked gcloud storage buckets create "gs://$Bucket" --location=$Region --uniform-bucket-level-access --project=$ProjectId
~~~

Grant scoped execution permissions, using the bootstrap administrator:
~~~powershell
Invoke-Checked gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$BuildSa" --role=roles/logging.logWriter
Invoke-Checked gcloud artifacts repositories add-iam-policy-binding $Repo --location=$Region --member="serviceAccount:$BuildSa" --role=roles/artifactregistry.writer --project=$ProjectId
Invoke-Checked gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member="serviceAccount:$BuildSa" --role=roles/storage.objectAdmin
Invoke-Checked gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member="serviceAccount:$BuildSa" --role=roles/storage.legacyBucketReader
Invoke-Checked gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member="user:$OwnerEmail" --role=roles/storage.objectAdmin
Invoke-Checked gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member="user:$OwnerEmail" --role=roles/storage.legacyBucketReader
Invoke-Checked gcloud iam service-accounts add-iam-policy-binding $RuntimeSa --member="user:$OwnerEmail" --role=roles/iam.serviceAccountUser --project=$ProjectId
Invoke-Checked gcloud iam service-accounts add-iam-policy-binding $BuildSa --member="user:$OwnerEmail" --role=roles/iam.serviceAccountUser --project=$ProjectId
Invoke-Checked gcloud projects add-iam-policy-binding $ProjectId --member="user:$OwnerEmail" --role=roles/cloudbuild.builds.editor
Invoke-Checked gcloud projects add-iam-policy-binding $ProjectId --member="user:$OwnerEmail" --role=roles/run.admin
Invoke-Checked gcloud projects add-iam-policy-binding $ProjectId --member="user:$OwnerEmail" --role=roles/serviceusage.serviceUsageConsumer
Invoke-Checked gcloud projects add-iam-policy-binding $ProjectId --member="user:$OwnerEmail" --role=roles/logging.viewer
Invoke-Checked gcloud artifacts repositories add-iam-policy-binding $Repo --location=$Region --member="user:$OwnerEmail" --role=roles/artifactregistry.reader --project=$ProjectId
~~~

These are an explicit learning setup, not a claim of a minimal custom production role.
Do not grant Owner/Editor, Secret Admin, or Cloud Run Admin to the runtime identity.
The dedicated build bucket permits source read and log write without project-wide storage access.
The build command's default LEGACY logging sends to both this bucket and Cloud Logging, hence the
build-account Logging Writer grant. To choose GCS_ONLY instead, use an explicit build YAML with
options.logging=GCS_ONLY and verify that mode; --gcs-log-dir alone does not select it.

For routine-operator handoff, the administrator finishes creation/grants and gives the operator
the nonsecret project/resource names and enabled secret version numbers. If bootstrap stopped
partway, inspect each owned account/repository/bucket/secret, skip only verified matching resources,
and resume the first unfinished step. Do not delete a collision or ignore every creation error.

## 4. Create secrets without putting keys in commands

In Secret Manager Console create dedicated secrets:
- research-learning-groq (Groq only)
- research-learning-tavily (both providers)

Paste key VALUES only into the Console's secret-value field. Do not paste into this document,
PowerShell command history, chat, build args or source code. Record the numeric active version;
pin that number in deployment rather than latest.

~~~powershell
$GroqVersion = '1'   # replace with your enabled numeric version
$TavilyVersion = '1'
Invoke-Checked gcloud secrets add-iam-policy-binding research-learning-groq --member="serviceAccount:$RuntimeSa" --role=roles/secretmanager.secretAccessor --project=$ProjectId
Invoke-Checked gcloud secrets add-iam-policy-binding research-learning-tavily --member="serviceAccount:$RuntimeSa" --role=roles/secretmanager.secretAccessor --project=$ProjectId
Invoke-Checked gcloud secrets versions list research-learning-tavily --project=$ProjectId
~~~

For Vertex, skip the Groq secret/grant. Version-list output is metadata, not secret content.
Do not use secrets versions access for troubleshooting output.
Secret creation, IAM grants and this metadata check are bootstrap-administrator operations.
At handoff give the routine operator the version numbers, or scoped Secret Manager Viewer
access to owned secrets. Listing versions never needs key-value access.

## 5. Build and identify the immutable image

Inspect the upload allowlist first; it must exclude .env, checkpoints, logs, .venv and gstack state.
~~~powershell
Invoke-Checked gcloud meta list-files-for-upload
Invoke-Checked gcloud builds submit . --project=$ProjectId --region=$Region --tag=$Image --service-account="projects/$ProjectId/serviceAccounts/$BuildSa" --gcs-source-staging-dir="gs://$Bucket/source" --gcs-log-dir="gs://$Bucket/logs"
$Digest = Invoke-Checked gcloud artifacts docker images describe $Image --project=$ProjectId --format='value(image_summary.digest)'
if ($LASTEXITCODE -ne 0 -or $Digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'No verified image digest' }
$ImageRef = "$Region-docker.pkg.dev/$ProjectId/$Repo/multi-agent-research@$Digest"
~~~

Expected: successful build and sha256 digest. Source and one copy of build logs live in the dedicated bucket; default logging also uses Cloud Logging;
it is not application persistence. Investigate build-service-account permissions on failure,
rather than assigning broad runtime roles. CI and Cloud Build serve different purposes.

## 6. Deploy Groq privately

Only run after P1 acceptance. Configure one service-level minimum/maximum, revision minimum
zero and revision maximum one. Keep CPU allocated after the HTTP response so in-process work
can continue. Eight HTTP requests is an initial API/poll setting, not eight allowed graph jobs.
Cloud Run scaling limits are not a strict data-consistency guarantee during replacement/rollout.

~~~powershell
$GroqModel = Read-Host 'Groq model ID verified available to your account'
$Settings = "MODEL_PROVIDER=groq,GROQ_CHAT_MODEL=$GroqModel,GCP_PROJECT_ID=,API_KEY=,ENABLE_TYPESAFE=false,LANGSMITH_TRACING=false,LANGCHAIN_TRACING_V2=false,DB_PATH=checkpoints.sqlite,REVIEW_TIMEOUT_MINUTES=60,CORS_ORIGINS=http://localhost:8080"
$Secrets = "GROQ_API_KEY=research-learning-groq:$GroqVersion,TAVILY_API_KEY=research-learning-tavily:$TavilyVersion"
Invoke-Checked gcloud run deploy $Service --project=$ProjectId --region=$Region --image=$ImageRef --service-account=$RuntimeSa --port=8080 --cpu=1 --memory=1Gi --timeout=600 --concurrency=8 --min=1 --max=1 --min-instances=0 --max-instances=1 --session-affinity --no-cpu-throttling --ingress=all --no-allow-unauthenticated --invoker-iam-check --set-env-vars=$Settings --set-secrets=$Secrets
Invoke-Checked gcloud run services add-iam-policy-binding $Service --project=$ProjectId --region=$Region --member="user:$OwnerEmail" --role=roles/run.invoker
$ServiceUrl = Invoke-Checked gcloud run services describe $Service --project=$ProjectId --region=$Region --format='value(status.url)'
$Revision = Invoke-Checked gcloud run services describe $Service --project=$ProjectId --region=$Region --format='value(status.latestReadyRevisionName)'
Invoke-Checked gcloud run services get-iam-policy $Service --project=$ProjectId --region=$Region
~~~

Expected: ready revision, intended runtime identity, Invoker IAM check enabled and no allUsers
or allAuthenticatedUsers binding. ingress=all allows the authenticated laptop proxy through;
IAM still controls access. Test raw service URL in an incognito browser: it must deny access
without authentication. If accessible, stop and repair IAM/check configuration before using keys.

For the Groq path GCP_PROJECT_ID is deliberately empty in the app: secrets are explicitly injected,
so config's Secret Manager fallback cannot unexpectedly activate an api-key secret.
Never “fix” UI 401 by making the service public.

## 7. Optional Vertex migration after Groq learning succeeds

~~~powershell
Invoke-Checked gcloud services enable aiplatform.googleapis.com --project=$ProjectId
Invoke-Checked gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$RuntimeSa" --role=roles/aiplatform.user
~~~

Check the available model and endpoint in Vertex Console/official model docs; set explicit
VERTEX_CHAT_MODEL and GCP_LOCATION (the model location is separate from Cloud Run region).
After R09 passes, the complete intended migration command is:
~~~powershell
$VertexModel = Read-Host 'Vertex model ID verified available to this project'
$VertexLocation = Read-Host 'Model endpoint location (for example global if supported)'
$VertexSettings = "MODEL_PROVIDER=vertexai,GCP_PROJECT_ID=$ProjectId,GCP_LOCATION=$VertexLocation,VERTEX_CHAT_MODEL=$VertexModel,API_KEY=,ENABLE_TYPESAFE=false,LANGSMITH_TRACING=false,LANGCHAIN_TRACING_V2=false,DB_PATH=checkpoints.sqlite,REVIEW_TIMEOUT_MINUTES=60,RESEARCHER_MODEL_OVERRIDE=,ANALYST_MODEL_OVERRIDE=,WRITER_MODEL_OVERRIDE=,CORS_ORIGINS=http://localhost:8080"
$VertexSecrets = "TAVILY_API_KEY=research-learning-tavily:$TavilyVersion"
Invoke-Checked gcloud run deploy $Service --project=$ProjectId --region=$Region --image=$ImageRef --service-account=$RuntimeSa --port=8080 --cpu=1 --memory=1Gi --timeout=600 --concurrency=8 --min=1 --max=1 --min-instances=0 --max-instances=1 --session-affinity --no-cpu-throttling --ingress=all --no-allow-unauthenticated --invoker-iam-check --set-env-vars=$VertexSettings --set-secrets=$VertexSecrets
~~~
The full set configuration replaces old environment/secret bindings rather than silently retaining
Groq keys or per-agent overrides. R09 must make explicit auth disable semantics unambiguous;
the current empty API_KEY is not sufficient with an existing api-key fallback secret.
No long-lived GCP key file is needed for the runtime account. Inspect the deployed revision's
nonsecret settings, secret references and effective per-agent model overrides in Console, then
do real inference. /ready reports the global model and cannot prove each agent's effective model.

Before using this path, fix/test R09's explicit API-key configuration semantics, or use a dedicated
project with no api-key secret and verify the runtime cannot fetch one. An empty API_KEY alone is
NOT proof fallback is disabled. Do not enable semantic TypeSafe auditing merely because Vertex works.
Export reports before switching providers; a new revision may have no old checkpoints.

scripts/deploy-vertex.ps1 currently assumes an existing service named multi-agent-research and
does not attach IAM tokens. Do not run it against this private learning service until R10 passes.
The owner-authenticated proxy acceptance below is the supported initial manual check.

## 8. Foreground acceptance through the authenticated proxy

Open a NEW PowerShell terminal. It does not inherit the first terminal's variables. Use this
self-contained sequence; choose the SAME project/region/account used for deployment:
~~~powershell
$ProxyProjectId = Read-Host 'Same learning project ID'
$ProxyAccount = Read-Host 'Same authorized Google account email'
$ProxyRegion = 'us-central1' # match your chosen service region
gcloud run services proxy multi-agent-research-learning --project=$ProxyProjectId --region=$ProxyRegion --account=$ProxyAccount --port=8080
if ($LASTEXITCODE -ne 0) { throw 'Proxy failed; check account, Invoker grant and port availability.' }
~~~
Leave it running. If port 8080 is occupied, select 8081 and use that port consistently in the
browser and health URLs below; do not kill an unknown process.
Adjust intended origin when changing the port. Keep the proxy local; test untrusted-origin
preflight rejection before paid use. CORS is a browser boundary, not authentication.

Open http://localhost:8080. The proxy adds your Google identity; the UI does not need a shared
API key. Keep one browser session so affinity cookies are retained.
Close the proxy terminal/press Ctrl+C after use; closing access does not cancel accepted jobs.

In another terminal:
~~~powershell
Invoke-RestMethod http://localhost:8080/health -TimeoutSec 15
Invoke-RestMethod http://localhost:8080/ready -TimeoutSec 15
~~~

Expected health OK and correct provider/model readiness. Then use the UI:
1. Enter a short disposable topic. It should be accepted promptly and show researcher progress.
2. Wait for researcher → analyst → writer → review, with no overlapping progress segments.
3. Check real sources, a readable draft and honestly labeled citation results.
4. Request a writing revision; confirm it returns to writer then review.
5. Request a research gap; confirm it returns to researcher, preserves old sources, then review.
6. Approve; confirm finalized report and save the raw Markdown to your own private file.
7. Try unknown thread, network interruption/retry and duplicate review; verify clear recovery.
8. Record time and provider/search usage/cost for this representative run.

Do not claim “202 in under 25ms”, exact token savings or perfect factual grounding without measurements.
There is no automatic forever polling requirement: set an operator deadline of ten minutes per run;
on expiry inspect logs, provider quotas and the known thread before doing anything else.

Closing the browser/proxy or aborting HTTP does NOT cancel _run_graph. There is no cancellation
endpoint. Through the working proxy inspect:
~~~powershell
$ThreadId = Read-Host 'Known thread ID from the accepted report'
Invoke-RestMethod "http://localhost:8080/research/$ThreadId" -TimeoutSec 15
~~~
If running=true, do not submit more work: wait/check logs; R05's slot remains occupied until the
worker ends. If finalized/awaiting_review/error, follow that state. If lost/unknown after replacement,
the app cannot recover the old worker. For abandoned disposable work, export any visible draft and
use the exact learning-service deletion in stage 10, verify absence, then redeploy. This loses SQLite
and is NOT per-job cancellation. The ten-minute operator deadline is not a server runtime/spend cap.

~~~powershell
Invoke-Checked gcloud run services logs read $Service --project=$ProjectId --region=$Region --limit=50
~~~

Logs may contain topics and provider error text today: inspect locally, redact before sharing.
A ready container does not prove inference, search or review resumption.

## 9. Record, update and roll back

In a private operator note record: source commit, image digest, revision, provider/model/location,
numeric secret versions, runtime identity, service settings, previous traffic and acceptance result.
Do not record secret values. Export finished Markdown before any replacement.

For this disposable first-deploy path, deploy the next reviewed digest using the same private command,
smoke it immediately, and restore a known-good revision if it fails. A temporary broken revision is
possible; this is not a zero-downtime production procedure. Drain active work and export first.
Use R10's corrected no-traffic private candidate script before reliable staged releases.
Stop and export active work before any update; do not treat browser close as a drain.

~~~powershell
Invoke-Checked gcloud run services describe $Service --project=$ProjectId --region=$Region --format='json(status.traffic)'
$KnownGoodRevision = Read-Host 'Previously recorded known-good revision'
Invoke-Checked gcloud run services update-traffic $Service --project=$ProjectId --region=$Region --to-revisions="$KnownGoodRevision=100"
~~~

Recheck anonymous denial, proxy readiness and real lifecycle after rollback. A revision retained
in Cloud Run may start a fresh instance: **code rollback does not restore SQLite history**.
No previous revision exists on the first deployment; delete the learning service or redeploy a fixed
image instead. Avoid simultaneous operators and stale traffic restore; serialize deployment operations.

## 10. Finish the experiment and stop charges

Save reports/metadata first. To allow idle scale-to-zero, set service min zero while leaving
revision min zero; access can start it again, so this is not a guaranteed spending stop.
Verify service/revision minimum settings in Console after the command.
~~~powershell
Invoke-Checked gcloud run services update $Service --project=$ProjectId --region=$Region --min=0
~~~

To stop this dedicated learning compute service, after verifying its exact name/project:
~~~powershell
if ($Service -ne 'multi-agent-research-learning') { throw 'Unexpected service: inspect before deletion' }
Invoke-Checked gcloud run services delete $Service --project=$ProjectId --region=$Region
~~~

After deletion, verify this exact service is absent in:
~~~powershell
Invoke-Checked gcloud run services list --project=$ProjectId --region=$Region --format='table(metadata.name,status.url)'
Invoke-Checked gcloud artifacts repositories list --project=$ProjectId --location=$Region
Invoke-Checked gcloud storage buckets describe "gs://$Bucket"
Invoke-Checked gcloud secrets list --project=$ProjectId --format='table(name)'
~~~
These inventory commands do not delete remaining resources. Confirm service absence and identify
retained learning repo/bucket/secrets; an unexplained command failure is not proof of absence.
Secret inventory may require the bootstrap administrator or metadata-viewer permission;
do not grant key-value access merely to inventory resources.

Deletion is an owner-run final step and loses local checkpoints. Do not delete any other service.
Images, build-source/log objects, secrets and logs can still incur storage charges. Review ONLY the
learning repository/bucket/secrets in Console, set appropriate retention/lifecycle, and remove resources
only after confirming ownership and desired retained records. Do not delete the whole project by default.

## Troubleshooting map

| Symptom | Next check |
|---|---|
| Permission denied enabling/creating | Bootstrap/admin identity and organization policy; do not make runtime admin |
| Build cannot stage/log/push | Build SA, dedicated bucket metadata/object roles and repository writer |
| Container not ready | Revision logs, PORT, non-root permissions, required secret versions/config |
| Raw URL works anonymously | IAM Invoker check and public bindings; stop paid use until private |
| Proxy 403 | Active Google account, per-service Invoker, ingress and organization restrictions |
| UI gets 401 through proxy | Unexpected application API_KEY (including fallback), not Google IAM |
| Model 404/403/429 | Provider model availability, Vertex location/role, quota/billing or Groq key |
| Stuck status after replacement | Lost worker/job flags/checkpoints; no current durable recovery guarantee |
| Failed candidate/traffic mismatch | Preserve recorded state; stop concurrent rollout, inspect before restore |
| Idle costs | Minimum/CPU configuration, tagged revisions, storage/build retention and provider usage |

## Installation and conflicting documentation

Official installation entry points: [Git for Windows](https://git-scm.com/install/windows),
[uv](https://docs.astral.sh/uv/getting-started/installation/),
[Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install),
[Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/).
With uv, install/select Python 3.13 as required by pyproject.toml; do not substitute older Python.

R09/R10/R11 documentation fixes must make README point here, preserve an existing .env, show
the Uvicorn start command before curl, quarantine the old public/latest deployment recipe, and
align AGENTS/README isolated test recipes with PYTHON_DOTENV_DISABLED=1. The current README
is not an alternative approved deployment runbook.

## Official references checked for this plan

- [Cloud Run deploy flags](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy)
- [Authenticated local proxy](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/proxy)
- [Invoker/public access controls](https://docs.cloud.google.com/run/docs/authenticating/public)
- [User-managed Cloud Build account](https://docs.cloud.google.com/build/docs/securing-builds/configure-user-specified-service-accounts)
- [Cloud Build submit flags](https://docs.cloud.google.com/sdk/gcloud/reference/builds/submit)

Cloud flags and IAM recommendations are documentation-grounded; successful live commands and costs
remain owner acceptance checks, not verified cloud outcomes.
