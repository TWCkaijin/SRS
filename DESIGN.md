# SRS Web interface design

Updated: 2026-11-16

SRS is a Traditional Chinese research host management console, implemented with
React 19, TypeScript and Vite, served by FastAPI. Apple HIG informs hierarchy,
legibility, materials, focus and feedback; semantic HTML and browser conventions
remain authoritative. No Apple-native framework is used.

## Navigation and monitoring

- The default landing page is the overview Dashboard. Main destinations are
  overview, history and alerts, Linux accounts, live host monitoring, Slurm setup,
  and scheduled monitoring. Existing hash routes remain valid.
- Opening overview/history never starts remote monitoring. Explicitly started
  monitoring continues when navigating within the signed-in app. Stop, logout,
  page exit or lease expiry ends it. There is no unattended monitoring promise.
- Cached data always has timestamps. Overview labels readings older than 60
  seconds as old records; no inventory GPU count is substituted for live capacity.
- Each host occupies a full-width horizontal row on desktop: left identity/status
  with CPU/RAM and a compact timestamp; middle GPU summary; right vertical actions.
  There is no separate timestamp row or bottom action bar. Narrow screens keep
  actions at the right of the host summary and give GPUs the full card width. Only the first two
  GPUs are shown initially, with the remaining count clearly labeled.
- A per-host expand button reveals remaining GPUs in a separate full-width grid,
  storage/network, GPU processes and hardware profile below the stable summary. Expanded rows are independent.
  Successful GPU observations remain authoritative over stale inventory.
- Login sessions (`who`) appear at the top of Account Management, refreshed every
  30 seconds from the existing monitor state only. Unmonitored, expired, failed or
  stale readings never appear as current login counts. Counts deduplicate usernames.
  NVIDIA compute processes remain next to host telemetry. Missing, unsupported and empty results have different messages.
- History retains seven days of approximately 30-second samples, plus alert
  transitions. Charts expose a table and distinct solid/dashed series; missing
  periods are not plotted as continuous observations.
- Alerts are local read-only records. Missing GPU or storage telemetry does not
  imply an existing resource alert recovered. No external notifications are sent.

## Account administration

- Linux account listings show UID >= 1000 except UID 65534 (nobody); system
  accounts are hidden from the management view. Existing SSH-account and server
  mutation protections still apply. The full backend inventory remains available
  for plan precondition and name-conflict checks.
  Website identity/session records remain separate: the existing environment-based
  website administrator authentication model is unchanged.
- Account listing is read-only. New, edit, password reset, password lock/unlock and
  deletion flow through a review panel inside the account modal.
- Review shows host, target, argv, full runner/preconditions, impact, failure policy,
  ten-minute expiry and content hash. Typed username and a review checkbox are
  required before execution. Destructive approval uses danger styling.
- Secrets are entered only at execution and cleared afterwards. Plan storage and
  audit events exclude them. SSH identity changes invalidate the plan. A plan is
  single-use, including an unconfirmed/partial execution.
- Deletion preserves the home directory and refuses an account with running
  processes. Password locking does not revoke SSH keys or terminate sessions.

## Presentation and accessibility

- Reuse the existing Apple-inspired semantic tokens, theme preference and web
  controls. Overview uses four summary cards; tables remain horizontally scrollable
  inside their containers on narrow viewports.
- Dialogs render at the document root to avoid transformed ancestors constraining
  the overlay. Existing focus trap, Escape handling and focus restoration remain.
- Respect reduced motion, contrast and transparency preferences; keep visible
  keyboard focus and explicit text for status. No platform-only dimensions or APIs.

## Validation

See `docs/OBSERVABILITY_ACCOUNTS_VERIFICATION.md` for the exact testing boundary,
commands, screenshots and remaining physical-host validation.

## Installed design guidance

- Full project-local `apple-hig` skill from `daft2/apple-hig-skill`, installed with
  the guide's skills CLI; source tracking lives in `skills-lock.json`.
- `AGENTS.md` applies the guidance to this React web app. Skill summaries are
  third-party guidance, not Apple requirements or browser standards.
- November 16 refresh: quieter solid content surfaces, restrained shadows,
  consistent headings and larger supporting text, grouped form/table styling,
  neutral navigation selection and color reserved for actions and status.
- Existing appearance preference is preserved as a product decision. Translucency
  is confined to chrome with opaque fallback; reduced-motion and contrast rules
  remain. No native APIs, point sizes or Apple font assets are introduced.

- Terminal uses an unpadded inner fit container and a padded outer frame, including
  28px below the terminal viewport to keep the prompt fully visible.

- Active monitored host headers show server-to-host ICMP Ping round-trip ms between
  identity and status. Stopped/unselected hosts hide it. One echo per sampling
  cycle runs alongside SSH with a 2s timeout; it never runs during profile-only
  inventory. Unavailable Ping and no-response states are separate from SSH status.
  Readings older than 30s are labeled expired.

- Ping header includes a 30-sample in-memory sparkline and current ms label.
  Samples deduplicate by timestamp, use elapsed-time spacing, and break on failed
  samples or gaps over 30s. Stopping or a new monitoring generation resets it.
  The local vertical scale is exposed in the accessible label and tooltip.

- Host header monitoring status uses 離線 for inactive/failed monitoring, 連線中
  while awaiting the first response, and 已連線 for successful fresh readings.

- Horizontal host rows use Apple HIG grouping and progressive disclosure adapted
  to semantic web buttons with aria-expanded/aria-controls; primary host actions
  remain visible without opening the details.
