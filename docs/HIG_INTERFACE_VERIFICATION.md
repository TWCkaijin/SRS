# Apple HIG web refresh — 2026-11-16

Installed the full daft2/apple-hig-skill package in .agents/skills/apple-hig;
verified SKILL.md and all seven references, with source hash in skills-lock.json.
AGENTS.md scopes the third-party guidance to React, browser behavior and web
accessibility. DESIGN.md records the equal-height card requirement and exceptions.

## Verification

- npm --prefix frontend test: 35 tests in 12 files passed.
- npm --prefix frontend run build: TypeScript and Vite production build passed.
- No lint script exists; no standalone lint claim is made.
- work/run-hig-browser.py: production assets, read-only local host inventory,
  synthetic 1/6/2/1 GPU readings. Existing monitor cache had expired, so this is
  layout evidence, not fresh remote GPU validation. No SSH commands sent.
- Four collapsed desktop cards each measured 728px; GPU viewports each 216px.
  Six-GPU content measured 416px and keyboard End scrolled it successfully.
- Long storage labels did not overlap numeric percentages. 390/768/1440 widths
  and 200% CSS zoom passed page overflow checks. Dark and light captured.
- work/hig-features-browser.mjs: isolated FastAPI/SQLite and synthetic SSH adapter
  on port 8017. Dashboard/history, account plan approval/create/delete, modal
  Escape/focus restoration, monitoring across navigation, mobile navigation,
  Slurm and schedules checked. 320/390/768/1440 layouts passed; no page errors.
- Inspected desktop/mobile screenshots, fixed closed mobile sidebar shadow and
  recaptured. Browser checks include keyboard GPU scroll, dialog focus and reduced
  motion. No screen-reader audit or full WCAG conformance assessment was run.
- Backend logic unchanged; full backend tests not repeated. Physical host accounts
  and monitoring configuration unchanged by this verification.

Evidence: frontend/evidence/hig-monitor-report.json and
frontend/evidence/hig-features-browser-report.json, plus hig-*.png screenshots.
