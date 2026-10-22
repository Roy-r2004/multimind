# multimind

Multi-model AI council app — ask one question, get answers from multiple LLMs, then a synthesized verdict. Includes brain memory, disagreement lessons, and an enterprise admin console.

## Quick start (Docker)

```bash
docker compose up --build
```

Open http://localhost:3080 — demo login `chafic@gmail.com` / `password123` (admin: `admin@gmail.com` / `password123`).

This starts Maps Census in Google Places mode (`MAPS_DISCOVERY_PROVIDER=google_places`, the default in `.env.example`) and does **not** start the `gosom` container — it's an alternative discovery provider behind its own Compose profile, not a required service.

### Maps Census with Gosom instead of Google Places

Set both `MAPS_DISCOVERY_PROVIDER=gosom` **and** `COMPOSE_PROFILES=gosom` (e.g. in your `.env`), then:

```bash
docker compose --profile gosom up --build
```

(`COMPOSE_PROFILES=gosom` in `.env` makes a plain `docker compose up --build` equivalent — either works, just pick one so the two settings don't drift apart.)

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
