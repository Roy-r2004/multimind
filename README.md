# multimind

Multi-model AI council app — ask one question, get answers from multiple LLMs, then a synthesized verdict. Includes brain memory, disagreement lessons, and an enterprise admin console.

## Scraping blueprint foundation (Phase 1A)

Country scraping missions store canonical ISO alpha-2/alpha-3 and continent metadata.
The backend-owned prompt template is
`backend/app/prompts/scraping/country_maximum_coverage_blueprint_v1.jinja2`.

Blueprint generation uses the existing OpenRouter integration:
`OPENROUTER_BLUEPRINT_RESEARCH_MODEL`,
`OPENROUTER_BLUEPRINT_STRUCTURING_MODEL`,
`OPENROUTER_BLUEPRINT_TIMEOUT_SECONDS`, and
`OPENROUTER_BLUEPRINT_MAX_OUTPUT_TOKENS`.

## Quick start (Docker)

```bash
docker compose up --build
```

Open http://localhost:3080 — demo login `chafic@gmail.com` / `password123` (admin: `admin@gmail.com` / `password123`).

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

## Local voice transcription

Voice transcription runs locally with Faster-Whisper. Defaults use `medium` on CPU with `int8`, concurrency `1`, beam size `1`, and the persistent `/models/whisper` cache when using Docker Compose. English and French are supported; auto-detect is limited to English/French output.

For a GPU deployment, explicitly set `TRANSCRIPTION_DEVICE=cuda`, `TRANSCRIPTION_MODEL=large-v3-turbo`, and `TRANSCRIPTION_COMPUTE_TYPE=float16`. If strict device mode is false and CUDA initialization fails, the service falls back to CPU `medium` with `int8`.
