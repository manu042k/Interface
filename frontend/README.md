# CUA Console (frontend)

Next.js 16 (App Router) + shadcn/ui. Design language: `DESIGN.md`
(`npx getdesign@latest add zapier` — warm cream, coffee ink, one orange CTA).

## Pages

| Route | What |
|---|---|
| `/` | **New run** — goal + target + typed params → `POST /runs`, redirects to the live run |
| `/runs/[id]` | **Live run** — interactive **noVNC** canvas of the sandbox + streaming **event timeline** (`/ws/runs/[id]/events`) + collapsible **sandbox terminal** (`/ws/runs/[id]/terminal`). When the run is `stuck`: claim → take control (drive the canvas) → hand back. |
| `/runs/[id]/report` | **Report** — printable cards: run summary, timeline, replay invocations, artifact schema, evidence screenshots. Print/PDF + download `.md`. |
| `/capabilities` | Approved catalog + invoke dialog (deterministic replay, outcome badge) |
| `/review` | Draft artifacts → sheet with ranked locators + rationale, risk, known/recoverable rules → approve/reject |
| `/interventions` | Open stuck runs → jump to the run to take over |

## Run

```bash
# backend (repo root)
bash backend/sandbox_image/build.sh          # once
bash start.sh                                # MockBank :8799, gateway :8080 (CUA_USE_SANDBOX=1)

# frontend
cd frontend
npm install
npm run dev                                  # http://localhost:3000
```

`NEXT_PUBLIC_API_BASE` defaults to `http://localhost:8080`; override in
`frontend/.env.local` if the gateway is elsewhere.
