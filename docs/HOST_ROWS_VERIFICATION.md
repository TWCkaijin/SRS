# Horizontal host rows — 2026-11-16

Apple HIG Skill grouping/progressive-disclosure guidance applied to existing web
controls. Each desktop host is a full-width row. CPU/RAM and the first two GPUs
are visible; remaining GPU count is labeled. Expand reveals all GPUs and opens
storage/network, GPU process and hardware details. Each host expands independently.
Phone layout stacks sections; host actions remain visible.

Validation: 41 frontend tests passed; TypeScript/Vite production build passed.
Browser fixture verified two GPUs initially, all six on expansion, hardware
profile open, other host unaffected, collapse, and 390/768/1440 overflow checks.
Desktop, expanded and mobile screenshots were inspected. The browser used
synthetic telemetry and existing local inventory; no remote changes were made.
No full screen-reader audit or backend test suite was run for this frontend change.

Evidence: frontend/evidence/host-rows-{desktop,expanded,mobile}.png.
