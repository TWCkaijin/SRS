# SRS web console

React / TypeScript frontend for the approval-first control plane. Every page uses the server's `/api/v1` API; no fixture or synthetic resource data is embedded in the application.

## Development

```sh
pnpm install --frozen-lockfile
pnpm dev
```

Vite serves `http://127.0.0.1:5173` and proxies `/api` to `http://127.0.0.1:8000`. Set `SRS_API_TARGET` when the backend runs on a different local port. Routes use URL fragments, so the production static server does not need an SPA fallback.

```sh
pnpm typecheck
pnpm test
pnpm build
```

The production build is emitted to `dist/` and should share an origin with the API. Session cookies stay HTTP-only; mutation requests carry the CSRF token returned by `/session`.

## Interaction boundaries

- Creating projects and drafts stores metadata; it does not contact remote hosts.
- Plan approval sends the exact displayed `content_hash` only after review acknowledgement. A changed hash resets that acknowledgement.
- Schedule approval and enablement are separate actions. An expiry is required and stale authorization cannot be enabled by the UI.
- Execution stop means stopping future dispatch. Remote termination remains a separately reviewed cancellation plan.
- Host and job pages read cached observations. Missing values are never displayed as measured zero.
- EventSource refresh events update cached views; execution logs track a cursor separately for stdout, stderr and audit streams. Full logs are downloaded from the backend.
- The environment mode stays visible on every page. Demo behavior is owned by the backend, never silently substituted in the browser.

Authorization and ownership are always enforced by the backend. Disabled controls provide guidance and are not a substitute for server checks.
