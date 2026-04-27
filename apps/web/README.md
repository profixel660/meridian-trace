# @meridian/web

Operator UI shell for Meridian. Stub scaffold — only `/` (project status) and `/health` exist today.

## Setup

```bash
cd apps/web
npm install
npm run dev
```

The UI expects the FastAPI backend on `http://localhost:8000`. From the project root:

```bash
uv run uvicorn meridian.api.main:app --reload --port 8000
```

Override the API base by setting `NEXT_PUBLIC_MERIDIAN_API` in `.env.local`. Future sessions will add project create/list, doc import, run, quarantine review, and export.
