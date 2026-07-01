# multimind

Multi-model AI council app — ask one question, get answers from multiple LLMs, then a synthesized verdict. Includes brain memory, disagreement lessons, and an enterprise admin console.

## Quick start (Docker)

```bash
docker compose up --build
```

Open http://localhost:3080 — demo login `chafic@acme.co` / `password123`.

## Deploy on Render

Connect this repo and use the included `render.yaml` blueprint. Set `OPENROUTER_API_KEY` on the API service.

## Local development

```bash
# Backend
cd backend && uvicorn app.main:app --reload --port 8001

# Frontend
npm run dev
```

See `.env.example` for configuration.
