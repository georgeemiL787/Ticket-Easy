# Ticket-Easy dashboard

The manager dashboard (React, Vite, TypeScript, Recharts). It reads the agent service's `/v1/dashboard/*` API and is
served by that service at `/dashboard`.

## Use it

```bash
cd services/agent && python scripts/seed_dashboard.py --reset                       # demo data (a few hundred conversations)
AGENT_STORE=sqlite AGENT_DB_PATH=var/demo.sqlite3 make run                 # API on http://127.0.0.1:8010
cd ../../apps/dashboard && npm ci && npm run build                                   # writes services/agent/web/dashboard
# open http://127.0.0.1:8010/dashboard
```

While working on the app: `npm run dev` (http://localhost:5173/dashboard/, proxies `/v1` to the service on port 8010).

## Commands

| Command | What it does |
|---|---|
| `npm run types` | Regenerate `src/api/types/*.ts` from `services/agent/contracts/schemas/Dashboard*.schema.json` |
| `npm run types:check` | Fail if those files are out of date (the build runs it, so the app is never built on stale types) |
| `npm run typecheck` | `types:check`, then `tsc --noEmit` |
| `npm test` | Component and helper tests (vitest) |
| `npm run build` | `typecheck`, then build into `../../services/agent/web/dashboard` (not committed) |

If you change a response model in `services/agent/src/agent_service/api/dashboard.py`: run `python scripts/export_schemas.py` in
`services/agent/`, then `npm run types`, and commit the schemas and the generated types.

## Structure

- `src/api/` the service client (`client.ts`, `useApi.ts`) and the generated types
- `src/state/useFilters.ts` business, period and display language live in the address (`?tenant=&range=&lang=`), so a link
  shares the view; the nav links carry them from page to page
- `src/i18n/` English and Arabic texts (same keys, a test checks it); Arabic switches the page to right-to-left
- `src/components/` layout (nav, top bar), KPI tile, the loading / empty / error views (`Async`)
- `src/pages/` Overview, Conversations (+ detail), Escalations, Actions, Unanswered questions, Alerts
