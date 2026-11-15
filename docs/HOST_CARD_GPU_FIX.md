# Host card layout and missing GPU correction — 2026-11-15

The cached initial profile for twcworker18 (WSL2) contained an empty GPU array
and `nvidia-smi 不可用或逾時`. Its later successful monitor snapshot at
2026-11-15T13:46:49.135Z contained an NVIDIA GeForce RTX 3080 Ti with
12288 MiB total memory, 2340 MiB used, and 8% utilization. NodeCard previously
created rows only from the cached hardware inventory, hiding valid later telemetry.

NodeCard now uses successful observed GPU identities as authoritative, with
inventory fallback on unavailable/failed queries. Unit tests cover empty initial
inventory, changed device identities, and error fallback without invented metrics.

Removed fixed GPU-list height and card stretching. Disk labels wrap independently
of nonwrapping percentages. Restored compact network rows and consistent inset
spacing for the login/process disclosure. Multi-GPU lists remain bounded/scrollable.

Validation: 35 frontend tests passed; production build passed. Browser replay of
existing actual cached telemetry passed at 390/768/1440px, with no page errors or
viewport overflow. Single-GPU list measured 74.6px; network rows measured 28.4px.
No new SSH probe or remote mutation was performed. Initial profile failure itself
was not retrospectively diagnosed; later successful GPU telemetry is confirmed.

Evidence: frontend/evidence/card-fix-report.json, cards-fixed-desktop.png,
twcworker18-gpu-fixed.png and twcworker18-gpu-fixed-mobile.png.
Harness: work/run-card-fix-browser.py and work/card-fix-browser.mjs.
