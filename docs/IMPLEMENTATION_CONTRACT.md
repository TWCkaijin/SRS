# Implementation contract

This project implements the user's approved specification. All remote actions require an immutable approved one-shot plan or an approved, unexpired read-only schedule. No external host is to be contacted while developing or testing without the user reviewing the generated plan. No real credentials in fixtures, git, command arguments, or logs.

## API (owned by main agent)

Base `/api/v1`. JSON objects use snake_case. All datetimes UTC ISO8601 with timezone. Lists return `{items: [...]}`. Errors `{detail: string}`. All mutating endpoints after login need `X-CSRF-Token`, obtained from GET `/session`. Session cookie HTTP-only. Credentials included in browser requests.

- GET `/health`: `{status,mode,version,configured}`. Mode `demo`, `local-slurm`, `remote-slurm` always displayed.
- POST `/session` `{username,password}`; GET `/session` -> `{username,csrf_token,permissions,mode}`; DELETE `/session`.
- GET `/nodes` -> node `{id,alias,display_name,hostname,ssh_username,enabled,inventory_revision,host_key_sha256,hardware,snapshot,observed_at}`. All reads cached only. Nodes 225/250 enabled, 226 excluded.
- GET `/projects`; POST `/projects` `{name,path,entrypoint,modes,container,revision}` registers only metadata. Initial demo project preseeded, includes `id,name,path,entrypoint,modes,container,revision`.
- GET/POST `/drafts`, GET/PATCH `/drafts/{id}`. Payload `{title,operation,host_ids,parameters}`; operations `preflight`, `submit_job`, `cancel_job`, `collect_results`, `deploy`, `write_config`, `cleanup`. parameters for job: `{project_id,profile:'cpu'|'single'|'ddp'|'fsdp2',cpus:1,memory_mb:2048,time_limit_minutes:5,args:[]}`. cancel `{job_id}`. deploy `{component:'slurm'|'apptainer'|'platform'}`. Other kinds require stored verified evidence; cannot approve a fabricated diff.
- POST `/drafts/{id}/plan` -> Plan. GET `/plans`, GET `/plans/{id}`. Plan `{id,draft_id,revision,title,status,content_hash,document,created_at,approved_at,approval_expires_at,issues}`. `document` `{schema_version,mode,authorization_kind,inventory,steps,impact,failure_policy,source}`. Each step `{id,host_id,title,kind:'ssh'|'slurm_api'|'file',argv,script,cwd,environment,request,file_change,reason,evidence,requires_sudo,execution_user,timeout_seconds,preconditions,on_failure}`. Secret envs stored as references, never values. `issues` unresolved issues; nonempty issues block approval.
- POST `/plans/{id}/approve` `{content_hash}` -> `{plan,execution}` (atomic approval + queue). POST `/plans/{id}/reject` `{reason}`. Changed hash/expired/invalidated cannot execute.
- GET `/executions`, GET `/executions/{id}` -> `{id,plan_id,schedule_id,status,mode,created_at,started_at,finished_at,steps,log_complete,error}`. GET `/executions/{id}/logs?after=0&limit=200&stream=stdout|stderr|audit` -> `{items:[{id,sequence,step_id,host_id,stream,text,created_at}],next_cursor}`. GET `/executions/{id}/download` NDJSON attachment. POST `/executions/{id}/stop` only stops future dispatch; executing remote termination needs separately approved cancellation plan unless in the original plan.
- GET `/schedules`; POST `{title,operation:'host_snapshot'|'job_status'|'job_logs',host_ids,interval_seconds:5,starts_at,expires_at,scope:{job_ids:[]}}`. GET/PATCH `/schedules/{id}`. POST `/{id}/plan` -> schedule itself with `{plan_document,content_hash,status:'awaiting_approval',revision}`. POST `/{id}/approve` `{content_hash}`. POST `/{id}/enable`; POST `/{id}/disable`. Independent `schedule.*` capabilities. Changing a schedule disables old authorization and requires replan/reapproval. Expiry mandatory, no overlap/catch-up.
- GET `/jobs`, `/jobs/{id}` cached only. GET `/jobs/{id}/artifacts` registered artifact IDs only, `/artifacts/{id}/download` ownership/path constrained. Job `{id,slurm_job_id,project_id,profile,status,execution_id,owner,created_at}`.
- GET `/events` SSE heartbeat and refresh events. Browser reconnect uses cached snapshot and execution log cursor.

## Executor adapter boundary (executor agent owns transport.py, remote_runner.py, redaction.py, adapters/)

Adapter async method `run_step(step: dict, context: dict, emit: async Callable[[str,str],None]) -> dict` result `{exit_code,signal,timed_out,unknown,outputs:{},error?:str}`. Context has `execution_id,step_id,mode,host` public inventory, `data_dir`, `plan_hash`. Emit streams stdout/stderr/audit. No DB import. Never perform network activity in demo mode. Strict host key pinning and identity match; credentials from executor-only `.env.hosts`, no per-host fallback. All connection/runner actions must be manifest-described. Implement DemoAdapter (deterministic realistic simulation visibly tagged), SSHAdapter and SlurmAdapter with test fakes. Scheduler and authorization checks owned by main worker; adapters independently fail closed on mode/identity/host pin mismatch. Reviewable remote wrapper script includes durable run/step journal and stdout/stderr capture; no blind replay after disconnect. Redactor must handle values split across chunks and redact before persistence, including remote journal if used. Provide public `make_adapter(mode, credentials_file=None)` facade returning adapter and `StreamingRedactor` API `feed(text)->str`, `flush()->str`.

## Ownership

- Root: backend config/db/models/planner/API/worker/security, integration, root pyproject.
- Frontend agent: frontend/ only, browser accessibility and API integration.
- Executor agent: backend/srs/transport.py, remote_runner.py, redaction.py, backend/srs/adapters/, tests/test_transport.py, tests/test_redaction.py.
- Workload/infra agent: workloads/, infra/, docs/DEPLOYMENT.md, docs/TEST_MATRIX.md, docs/OPERATIONS.md, tests/test_workload_contracts.py. Do not execute remote operations or start VMs. Produce reviewed declarative scripts/templates which main planner can reference.

## Test boundaries

Local development may use SQLite with explicit `demo` label. Production schema MariaDB via SQLAlchemy/Alembic; do not call SQLite testing proof of MariaDB. Local read/write tests confined to tmp dirs. Actual host admin credentials absent; never auto-fill or copy user's SSH keys. Real deployments remain generated pending plans until user approval.

## Implemented extensions

- `collect_results` is a one-shot reviewed read-only plan with `{job_id}` on controller 225. It collects only rank-0/1.json, at most 64KiB each; downloads contain locally registered, masked data.
- `POST /executions/{id}/reconcile` requires `execution.reconcile`; it only reattaches mutating SSH journals or reconciles Slurm requests under the original approval. `reconciliation_supported` is returned. Interrupted read-only SSH has no remote journal and needs a new approved probe.
- Job log schedules use manifest-declared runtime byte-offset substitutions from persistent JobLogCursor records created when the job is registered. Missing cursors fail closed; truncation/rotation records gaps.
- Optional preflight parameters `cluster_probe` and `deployment_probe` use the fixed scripts and parsers documented in EVIDENCE_CONTRACT.md.

## Browser-managed SSH and live monitoring (2026-11-15)

Observation is a separate real-only capability, independent of demo/mutation adapters. All inventory nodes and web-registered hosts use the same flow; workload eligibility is not a monitoring restriction.

`POST /ssh-hosts` requires `hosts.manage` plus CSRF, accepts only SSH connection settings, and queues the fixed hardware profile probe. Passwords are encrypted with a local 0600 Fernet key; only public connection metadata, credential-presence flags and profiles are returned. Empty passwords on edits preserve the encrypted secret; revision checks prevent stale edits. `POST /ssh-hosts/{id}/profile` queues a fresh profile. Unknown/changed SSH fingerprints are returned as pending trust; `POST /ssh-hosts/{id}/trust` requires the exact pending fingerprint and revision before credentials can be used against that key.

`POST /monitor` requires `monitor.manage` plus CSRF and accepts registered host IDs and a 1/2/5/10-second interval. It binds both inventory and SSH-configuration revisions to a generation. `/monitor/heartbeat` renews a 45-second lease; `/monitor/stop` revokes only the matching generation. `GET /monitor` is a cached read scoped to the login session. Logout, expired authentication, settings changes and lease expiry prevent dispatch/publication.

The separate `srs monitor` process decrypts credentials, verifies SSH host identity, executes fixed Linux Python probes, and keeps independent sampling clocks per host. Valid SSH connections are reused. Profiles persist CPU/memory/GPU/storage/network/OS metadata; telemetry reports usage, mount capacities, network counters and rates. Unknown first rates and reset counters stay unknown. The API never opens SSH. No arbitrary command, sudo, installation or remote file write is accepted by these routes. Demo/scheduled Node snapshots remain separate.

The development launcher starts the monitor idle. Existing valid `.env.hosts` records can be imported once; GUI records take precedence. This is an explicit real-observation capability in the demo control UI; job execution remains demo. New web observation nodes do not gain workload mutation eligibility.
