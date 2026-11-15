# Dashboard, monitoring history and reviewed Linux account management

Verified locally on 2026-11-15.

## Delivered behavior

- Default Dashboard with recent host count, observed GPUs/free VRAM, unresolved
  alerts, recorded jobs, website login sessions and login/monitor/account audit.
- Read-only `who` session records and NVIDIA compute processes (GPU, PID, owner,
  executable basename, VRAM). Full command arguments are not collected. Permissions,
  unsupported queries and empty results are distinct. These are visible beside
  live monitoring and in the last retained history sample.
- Seven-day monitoring history. Samples are retained approximately every 30 seconds
  plus alert transitions; 1h/24h/7d queries are bounded and bucketed to at most 480
  chart points with CPU/RAM peaks and an accessible data table. GPU, disk, network,
  login and process payloads are retained in each sample. Retention cleanup runs
  when new samples are persisted.
- Alert lifecycle: CPU >=95%, RAM >=90%, VRAM >=95%, disk available <=10%, and
  observation failures. Subsequent sufficient observations resolve an alert.
  A stopped monitor or unavailable GPU data does not falsely resolve it. These are
  site-local records, not email/LINE notifications, and there is no alert muting UI.
- All Linux accounts can be listed, including protected system accounts. Creation,
  display-name/password updates, password lock/unlock and deletion require a stored
  plan, explicit typed confirmation, review checkbox and `plan.approve` permission.
  Old direct account POSTs return 409. Read-only identities can list accounts.
- Plans last ten minutes, bind SSH host revision/fingerprint, account pre-state,
  command argv and fixed runner hash. Atomic claiming prevents replay. Remote
  execution rechecks account identity and refuses deletion with active processes.
  Passwords only travel via stdin at execution, never in argv, plan or audit.
  Deletion keeps home files. Password locking does not block public-key SSH.
- A running explicit monitor stays alive across in-app navigation, and ends on
  stop/logout/page exit/lease expiry. No remote monitor is auto-started on Dashboard.

## Validation performed

- `npm --prefix frontend run build`: TypeScript + production Vite build passed.
- `npm --prefix frontend test`: 11 files, 32 tests passed.
- `.venv/bin/ruff check backend tests migrations`: passed.
- `.venv/bin/python -m pytest -q`: 258 passed, 1 skipped. The existing Starlette
  httpx deprecation warning remains; it is not a test failure.
- Isolated SQLite Alembic upgrade to 0005, model/table/index parity, downgrade to
  0004 and upgrade again passed. No live MariaDB migration was performed.
- Browser acceptance uses real production assets, FastAPI HTTP requests and an
  isolated SQLite database, with a synthetic SSH adapter. It verifies Dashboard,
  history filtering/empty state, GPU/login panels, explicit monitoring across
  navigation, protected accounts, create/delete plan review, modal Escape/focus
  restoration, 320/390/768/1440px layouts, mobile navigation, dark appearance and
  reduced motion. No page errors or horizontal viewport overflow were observed.
- Screenshots were inspected. Modal rendering was moved to a document-root portal
  so an animated page ancestor cannot confine its backdrop.

Browser harness: `work/observability-browser-fixture.py` (127.0.0.1:8017) and
`work/observability-browser.mjs`. Machine-readable evidence:
`frontend/evidence/features-browser-report.json`.

Screenshots:

- `frontend/evidence/features-dashboard-desktop.png`
- `frontend/evidence/features-history-desktop.png`
- `frontend/evidence/features-account-review-desktop.png`
- `frontend/evidence/features-live-activity-desktop.png`
- `frontend/evidence/features-dashboard-mobile.png`
- `frontend/evidence/features-accounts-mobile.png`
- `frontend/evidence/features-dashboard-dark-mobile.png`

## Deployment and limits

Demo SQLite creates additive tables at startup. Live deployments must explicitly
run the existing `srs init-db` migration workflow before starting the updated API
and monitor. Restart both to enable the extended probe and persisted history.
Existing data is not retrospectively populated. Monitoring needs the monitor
process plus an explicit browser lease; the Dashboard is not an unattended agent.

No real host account was created, changed, locked or deleted in this acceptance
run. No real GPU/proc/who collection was exercised by the new browser harness;
physical-host acceptance remains separate. Website account provisioning is still
controlled by the existing environment configuration; the account management UI
manages Linux hosts. Existing terminal/SSH-setting/Slurm flows are not converted
into account plans. Screen-reader and live MariaDB validation were not performed.

Local runtime smoke check: API and monitor processes started on the existing demo
configuration. Authenticated `/observability` and 24-hour history reads returned
success; monitoring remained inactive and the smoke-test website session signed
out. There were no active leases or pending profile requests before starting the
monitor. The isolated 8017 fixture server was stopped after browser acceptance.
