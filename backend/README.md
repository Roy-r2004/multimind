# MultiAI Python Backend

Enterprise-grade FastAPI backend for multi-model AI orchestration with Jinja2 prompt templates.

## Architecture

```
backend/
├── app/
│   ├── main.py              # FastAPI application
│   ├── core/                # Config, auth, logging, dependencies
│   ├── db/                  # SQLAlchemy models + session
│   ├── schemas/             # Pydantic API contracts
│   ├── api/v1/              # REST endpoints
│   ├── services/            # Business logic layer
│   ├── llm/                 # Orchestrator + providers
│   │   ├── orchestrator.py  # Parallel model calls → verdict → insurance
│   │   ├── prompt_engine.py # Jinja2 template renderer
│   │   ├── providers.py     # OpenRouter gateway adapter
│   │   └── catalog.py       # Model registry + pricing
│   └── prompts/             # Jinja2 templates (version-controlled)
│       ├── system/          # base, model_answer, verdict, decision_insurance
│       └── partials/        # strategy_instructions, model_responses
└── scripts/seed.py          # System model sets + demo user
```

## Quick Start

### With Docker (recommended)

```bash
docker compose up --build
```

- API: http://localhost:8000/docs
- Demo user: `chafic@gmail.com` / `password123` (admin: `admin@gmail.com` / `password123`)

### Local development

```bash
# Start Postgres + Redis
docker compose up postgres redis -d

# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -e .
python -m scripts.seed
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
npm run dev
```

Set `VITE_API_URL=/api/v1` (default) — Vite proxies to port 8000.

## Environment

Copy `.env.example` to `.env`:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL async URL |
| `SECRET_KEY` | JWT signing key |
| `OPENROUTER_API_KEY` | **Required** — routes all models via OpenRouter |
| `OPENROUTER_SITE_URL` | Optional HTTP-Referer for OpenRouter rankings |
| `OPENROUTER_PRICING_CACHE_TTL_SECONDS` | How often to refresh model list prices (default 3600) |
| `PUBLIC_APP_URL` | Base URL for share links |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/auth/signup` | Register + create org |
| POST | `/api/v1/auth/signin` | Login → JWT |
| GET | `/api/v1/auth/session` | Current user + org |
| GET/POST | `/api/v1/chats` | Chat CRUD |
| POST | `/api/v1/chats/{id}/turns` | **Run multi-model turn** |
| GET/POST | `/api/v1/model-sets` | Model set management |
| GET/POST | `/api/v1/projects` | Projects |
| GET | `/api/v1/costs/summary` | Usage analytics |
| GET | `/api/v1/costs/pricing` | Live OpenRouter rates for catalog models |

## Prompt System (Jinja2)

All LLM prompts are rendered from templates in `app/prompts/`:

- `system/base.j2` — enterprise guardrails (included everywhere)
- `system/model_answer.j2` — independent model responder
- `system/verdict.j2` — strategy-aware synthesis (uses partials)
- `partials/strategy_instructions.j2` — Reconcile / Synthesize / Rank / Pick Best / Debate
- `system/decision_insurance.j2` — structured risk analysis

Edit templates and restart — no code changes required for prompt tuning.

## Turn Orchestration Flow

1. Client POSTs `/chats/{id}/turns` with `user_message` + `model_set_id`
2. Orchestrator runs N models **in parallel** via asyncio
3. Each answer persisted + cost recorded
4. Verdict model synthesizes using Jinja strategy prompt
5. Optional Decision Insurance pass
6. Full turn returned to client

## Frontend Integration

When logged in, the React app switches from mock data to the Python API:

- `src/lib/api/` — typed HTTP client
- `src/lib/auth.tsx` — JWT session management
- `src/lib/store.tsx` — API-backed chat/project/model-set state
- `src/routes/chat.tsx` — SSE streaming turns via `POST /chats/{id}/turns` + `GET /chats/turns/{id}/stream`
- `src/routes/shared.$token.tsx` — public share links via `GET /share/{token}`
- `src/routes/costs.tsx` — live data from `GET /costs/summary`

**Real LLM only** — set `OPENROUTER_API_KEY` from [openrouter.ai/keys](https://openrouter.ai/keys). All panel models use distinct OpenRouter slugs (GPT-4.1, Claude Sonnet 4, Gemini 2.5 Pro, DeepSeek V3, Mistral Large, Llama 3.3, Qwen 2.5).

### Alembic (PostgreSQL production)

```bash
cd backend
alembic upgrade head
```
