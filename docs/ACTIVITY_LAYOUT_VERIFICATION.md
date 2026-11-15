# Activity and terminal layout fixes — 2026-11-16

- Removed grid-wide equal row sizing and card stretching; equal GPU viewports remain.
- Moved terminal fitting into an unpadded inner mount. Outer frame reserves 28px
  bottom padding; measured final screen clearance was 43px in desktop Chromium.
- Login user summary now appears above account management. It reads /monitor every
  30 seconds without creating or renewing leases, gates results by monitored host,
  lease expiry, successful observation, and 60-second freshness. Counts deduplicate
  usernames. GPU process disclosures stay on monitoring and history pages.

Validation: production build passed; 39 tests in 13 files passed. Four new tests
cover refresh cadence, deduplication, stopped monitoring, stale data, request error
and timer cleanup. Browser verification used synthetic HTTP/WebSocket responses:
no remote SSH connection or physical-host changes. It checked second-row expansion
without first-row growth, terminal screen bounds, account summaries, GPU scrolling,
390/768/1440 responsiveness and 200% CSS zoom. Screenshots visually inspected.
No full screen-reader or backend suite run; backend code unchanged.

Evidence: frontend/evidence/activity-fix-report.json,
terminal-padding-fixed.png and accounts-login-users.png.
