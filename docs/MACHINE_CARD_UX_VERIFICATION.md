# Machine card UX refinement — 2026-11-16

Scope: existing React machine cards; Apple HIG web guidance and UI UX Designer
redesign audit. Inspected the user screenshot and actual component/style sources.

| Before | Implemented | Evidence |
|---|---|---|
| Timestamp occupies its own wide row | One-line metadata within left summary | DOM: 18px height; desktop/mobile screenshots |
| CPU/RAM separated from machine identity | Same left column, below host and hardware summary | CPU and left-summary x coordinates both 316.1875 at 1440px |
| Full-width bottom action strip | Right-side vertical action rail, no footer strip | Actions x 1259.8, GPU x 746.9; screenshot |
| Extra GPUs stretch upper summary | Two-GPU summary remains stable; remaining GPUs in full-width revealed grid | 2 visible collapsed, 6 expanded; other host unchanged |
| Phone GPU column leaves unused space | GPU summary spans full phone card width, actions remain beside host summary | 390px screenshot and 320px overflow check |
| Long expanded inventory hard to scan | Full-width detail area, two-column hardware facts on desktop | Expanded screenshot |

Verification:
- All 41 tests in 14 frontend test files passed.
- TypeScript + Vite production build passed. No standalone lint script exists.
- Browser checks: 320/390/768/1024/1440 widths, no page overflow or CPU label/value
  overlap; dark/light appearances; reduced motion; 200% CSS zoom; keyboard Enter
  expansion/collapse with aria-expanded; independent host expansion; first-two-GPU
  limit and full expanded data; missing GPU/CPU values, SSH/Ping failure, pending
  first observation. No page errors.
- Measured desktop card height: 215.6px with active Ping and all action controls.
- Browser transport is synthetic. Inventory is read-only local data. No real SSH
  commands, remote mutations, deployment or Git publication were performed.
- No full screen-reader audit, WCAG conformance claim or backend retest; backend
  behavior was not modified.

Machine-readable results: frontend/evidence/machine-refined-report.json.
Screenshots: frontend/evidence/machine-refined-{desktop,dark,mobile,expanded}.png.
