# Project Architecture

## 1. Project overview

MultiAI is a multi-model AI council application. A logged-in user asks one question, the app sends it to a selected "model set" of AI models, stores each individual answer, then asks a configured Verdict AI to synthesize a final verdict. The app also has sharing, projects, model-set management, prompt templates, cost tracking, an admin console, disagreement lessons, and a persistent "brain" profile that learns from completed lessons.

The current product framing is:

- Ask once.
- Compare multiple model answers.
- Produce one verdict.
- Let the user disagree with the verdict to build a structured lesson.
- Feed completed disagreement lessons into the user's brain/persona memory.

The seeded demo user is `Chafic` (`chafic@gmail.com`) and the seeded admin user is `Admin` (`admin@gmail.com`), both created by `backend/scripts/seed.py`.

## 2. Tech stack

Frontend:

- React 19 with TypeScript.
- TanStack Start and TanStack Router for full-stack React routing/SSR.
- TanStack React Query is provided at the root, though most current data fetching is done through custom API helpers and local React state.
- Vite 8 build/dev server.
- Tailwind CSS v4 with `@tailwindcss/vite`.
- shadcn-style UI setup in `components.json`, Radix UI primitives, and `lucide-react` icons.
- Frontend API client lives under `src/lib/api`.

Backend:

- Python 3.12 FastAPI app in `backend/app`.
- SQLAlchemy async ORM with SQLite for local defaults and PostgreSQL for Docker/Render production.
- Alembic migrations in `backend/alembic`.
- Pydantic v2 schemas in `backend/app/schemas/api.py`.
- Jinja2 prompt templates under `backend/app/prompts`.
- OpenRouter is the only configured LLM provider adapter. It routes to OpenAI, Anthropic, Google, DeepSeek, xAI, and other OpenRouter models by slug.

Package/runtime clues:

- `package.json` uses npm scripts and `package-lock.json`.
- `bun.lock` and `bunfig.toml` also exist, but the scripts and Dockerfile use npm.
- Frontend production build outputs to `.output` and starts with `node .output/server/index.mjs`.
- Docker Compose runs separate `web`, `api`, `postgres`, and `redis` services.
- Render deployment is described in `render.yaml` as two Docker web services plus a managed Postgres database.

## 3. Folder structure

Root files:

- `package.json`: frontend scripts and dependencies. Scripts are `dev`, `build`, `build:dev`, `preview`, `start`, `lint`, and `format`.
- `vite.config.ts`: Vite/TanStack Start setup. It registers Tailwind, TanStack Start, Nitro, React, tsconfig paths, and a dev proxy from `/api/v1` to `BACKEND_PROXY_TARGET`.
- `tsconfig.json`: strict TypeScript config with `@/*` mapped to `src/*`.
- `components.json`: shadcn-style aliases and Tailwind settings.
- `.env.example`: shared frontend/backend environment example.
- `Dockerfile`: frontend build/runtime container.
- `docker-compose.yml`: local full-stack Docker setup.
- `render.yaml`: Render blueprint for API, web, and database.
- `scripts/start-web.sh`: starts the built TanStack/Nitro frontend server.

Frontend `src`:

- `src/start.ts`: TanStack Start instance and request error middleware.
- `src/server.ts`: custom server entry. Proxies `/api/v1` to the Python backend, then delegates other requests to TanStack Start server entry.
- `src/router.tsx`: creates the TanStack router with the generated route tree and a `QueryClient`.
- `src/routeTree.gen.ts`: generated TanStack Router route tree. Do not edit manually.
- `src/routes`: file-based routes for chat, projects, lessons, brain, admin, login, settings, templates, sharing, and model sets.
- `src/components`: app shell, modals, chat-specific components, cinematic visuals, admin UI, and shadcn UI primitives.
- `src/lib`: auth, API client, model catalog helpers, chat store, turn streaming/merging, cost helpers, error handling, and mock/shared types.
- `src/hooks`: local React hooks.
- `src/styles.css`: Tailwind v4 theme, CSS variables, utilities, and animations.

Backend `backend`:

- `backend/app/main.py`: FastAPI app factory, CORS, lifespan startup, audit middleware, exception mapping, and v1 router mounting.
- `backend/app/api/v1`: API route modules.
- `backend/app/services`: business logic for auth, chat/turns, model sets, projects, templates, costs, lessons, brain, admin, shares, and audit logs.
- `backend/app/llm`: LLM catalog, provider adapter, pricing, prompt engine, and turn orchestrator.
- `backend/app/db`: SQLAlchemy base, async session, and ORM models.
- `backend/app/core`: config, dependencies, exceptions, logging, and security.
- `backend/app/schemas/api.py`: Pydantic API request/response schemas mirrored by frontend TypeScript types.
- `backend/app/prompts`: Jinja2 system prompts and partials.
- `backend/alembic`: migrations.
- `backend/scripts`: seed script, entrypoint, and utility/test scripts.

## 4. App entry points

Frontend development:

- `npm run dev` runs `vite dev`.
- Vite proxies frontend requests beginning with `/api/v1` to `BACKEND_PROXY_TARGET` or `http://localhost:8001` by default in `vite.config.ts`.

Frontend production:

- `npm run build` creates the TanStack Start/Nitro output.
- `npm run start` runs `node .output/server/index.mjs`.
- The root `Dockerfile` builds with Node 22, copies `.output`, then starts `scripts/start-web.sh`.
- `src/server.ts` is the production/custom server entry. It proxies `/api/v1` to `BACKEND_PROXY_TARGET` or `http://localhost:8000`, strips the `host` header, forwards request bodies, and returns a JSON `502` when the API backend is unreachable.

TanStack Start:

- `src/start.ts` creates the Start instance with middleware that catches unexpected SSR errors and renders `src/lib/error-page.ts`.
- `src/server.ts` also normalizes a known h3 swallowed-error JSON shape into the same HTML error page.

Backend:

- `backend/app/main.py` exposes `app = create_app()`.
- Local README instructions run `uvicorn app.main:app --reload --port 8000` or `--port 8001` depending on setup.
- Docker uses `backend/scripts/docker-entrypoint.sh`, then serves FastAPI on `$PORT` default `8000`.
- In development, startup calls `Base.metadata.create_all`; production should rely on Alembic.

## 5. Routing architecture

The frontend uses TanStack Router file-based routing:

- Route source files are in `src/routes`.
- `src/routeTree.gen.ts` is generated by TanStack Router and imports every route module.
- `src/router.tsx` passes `routeTree` into `createRouter`.
- `src/routes/__root.tsx` defines the root shell, document head, error UI, not-found UI, and root providers.

Important routes:

- `/` (`src/routes/index.tsx`): renders `ChatPage` from `src/routes/chat.tsx`.
- `/chat`: main chat/verdict screen.
- `/login`: authentication.
- `/model-sets`: model-set management.
- `/projects` and `/projects/$id`: project list/detail.
- `/templates`: reusable prompt templates.
- `/settings`: profile, default model set, model library search, strategy descriptions.
- `/lessons` and `/lessons/$id`: disagreement lesson list/detail.
- `/brain`: user brain/persona memory.
- `/shared/$token`: public read-only shared chat.
- `/admin/...`: admin console routes under `src/routes/admin`.

Root providers in `src/routes/__root.tsx` wrap the app in:

- `QueryClientProvider`
- `AuthProvider`
- `ModelsProvider`
- `ChatStoreProvider`

## 6. Main user flows

Chat flow:

1. The user logs in and lands on `/chat` or `/`.
2. `ChatPage` gets active model set/chat state from `useChatStore`, auth from `useAuth`, and model metadata from `useModels`.
3. On send, `src/routes/chat.tsx` creates a chat if needed through `api.chats.create`.
4. It optionally builds `custom_instructions` from a selected prompt template, reference chat, and placeholder file metadata.
5. It calls `api.chats.createTurn(auth, chatId, { user_message, model_set_id, custom_instructions })`.
6. The backend creates a pending `Turn` and pending `ModelAnswer` rows.
7. The frontend calls `runTurnInBackground`, which streams events from `/api/v1/chats/turns/{turnId}/stream`.
8. `src/lib/turnRunner.ts` and `src/lib/turnState.ts` merge streamed events into an in-memory per-chat turn cache.
9. `ChatPage` renders the user message, each model answer card, then the final verdict card.

Verdict flow:

- Backend orchestration is in `backend/app/llm/orchestrator.py`.
- Phase 1 calls all model-set `models` in parallel through OpenRouter.
- Phase 2 sends the collected model answers to the configured `verdict_model`.
- The verdict prompt is rendered by `PromptEngine.verdict_prompt` using `backend/app/prompts/system/verdict.j2` and partials.
- The verdict is stored in the `verdicts` table and returned in `TurnResponse.verdict`.

Decision insurance legacy data:

- Decision Insurance is currently disabled as an active product flow.
- `TurnCreateRequest.decision_insurance_enabled`, `Turn.decision_insurance_enabled`, `DecisionInsurance`, and `UsageKind.INSURANCE` remain for backward compatibility with existing stored data.
- `ChatService.start_turn` writes new turns with `decision_insurance_enabled=False`.
- `TurnOrchestrator.run` now stops after model answers and the final verdict; it does not call the decision insurance prompt or create insurance rows/cost records.
- Normal and shared chat UIs no longer render a Decision Insurance section.

Disagreement/lesson flow:

1. The verdict card has an `I disagree` button when no lesson exists.
2. `VerdictDisagreeModal` collects `reason` and `user_position`.
3. `api.lessons.disagree` posts to `/api/v1/lessons/turns/{turnId}/disagree`.
4. `LessonService.disagree_with_verdict` verifies the turn is completed/partial and has a verdict.
5. It creates a `VerdictLesson` row with the user's disagreement and the original verdict.
6. It uses the verdict model provider/model to generate structured lesson JSON via `verdict_lesson.j2`.
7. It stores title, summary, comparison JSON, token/cost data, and marks the lesson completed or failed.
8. On successful lesson creation, it calls `brain_service.learn_from_lesson`.
9. The frontend navigates to `/lessons/$id`.

Persona/brain flow:

- `backend/app/services/brain_service.py` owns user brain memory.
- `GET /api/v1/brain` returns the user's current brain profile.
- `BrainService.get_context_for_user` formats the brain profile and recent memories into prompt context.
- `ChatService.execute_turn_stream` includes this brain context in normal model-answer and verdict prompts.
- `BrainService.learn_from_lesson` updates the brain only from completed lessons, using `DEFAULT_BRAIN_MODEL = "gpt-4.1"`.
- Brain memory stores summary, thinking style, likes, dislikes, recent lesson memories, and lesson count.

Project/template/share flows:

- Projects organize chats but deleting a project only unassigns chats.
- Templates are reusable instructions that can be inserted into model-set custom instructions or the chat composer.
- Share links are created from chat and expose read-only chat turns at `/shared/$token`.

## 7. AI/provider architecture

Provider layer:

- `backend/app/llm/providers.py` defines an abstract `LLMProvider`.
- `OpenRouterProvider` is the only concrete implementation.
- `ProviderRegistry.get_provider(_provider_name)` always returns the OpenRouter provider, regardless of provider name.
- All LLM calls go to `https://openrouter.ai/api/v1/chat/completions`.
- OpenRouter headers use `OPENROUTER_API_KEY`, optional `OPENROUTER_SITE_URL`, and `OPENROUTER_APP_NAME`.

Current built-in AI catalog:

- `gpt-4.1` -> `openai/gpt-4.1`
- `claude` -> `anthropic/claude-sonnet-4`
- `gemini` -> `google/gemini-2.5-pro`
- `grok` -> `x-ai/grok-4`
- `mistral` -> `mistralai/mistral-large-2512`
- `deepseek` -> `deepseek/deepseek-chat-v3-0324`
- `llama` -> `meta-llama/llama-3.3-70b-instruct`
- `qwen` -> `qwen/qwen-2.5-72b-instruct`

The frontend model color map also knows `grok`, `deepseek`, `mistral`, `llama`, and `qwen`. `src/lib/modelIds.ts` knows how to label `x-ai` slugs as `xAI`.

Dynamic models:

- Backend can search OpenRouter pricing/catalog metadata and add organization models.
- Dynamic internal ids use `or:` plus the OpenRouter slug with `/` converted to `--`.
- The frontend has matching helpers in `src/lib/modelIds.ts`.

Response processing:

- Model answer prompts may include a `CONFIDENCE: N` marker. `LLMProvider.parse_confidence` strips it and stores confidence.
- Verdict, lesson, and brain update calls expect JSON and parse it with `parse_json_response`.
- Costs are resolved from OpenRouter reported cost when present or catalog pricing estimates otherwise.

Verdict logic:

- Strategy values are `Reconcile`, `Synthesize`, `Rank`, `Pick Best`, and `Debate`.
- Backend strategies are enums in `backend/app/db/models.py` and `backend/app/schemas/api.py`.
- Frontend strategy descriptions are in `src/lib/mock.ts`.
- `backend/app/llm/prompt_engine.py` currently maps all strategies to `system/verdict.j2`; strategy-specific behavior comes from prompt context/partials, not separate files.

## 8. State/data flow

Auth:

- `src/lib/auth.tsx` stores auth/session state and returns `{ token, orgId }` headers for API calls.
- Backend auth dependencies in `backend/app/core/dependencies.py` resolve the current user/org from JWT and `X-Org-Id`.

Frontend API:

- `src/lib/api/client.ts` resolves API base from a document meta tag named `api-base`, then `import.meta.env.VITE_API_URL`, then `/api/v1`.
- `src/routes/__root.tsx` sets `api-base` from `API_PUBLIC_URL` when available.
- `src/lib/api/index.ts` contains typed endpoint wrappers.
- `src/lib/api/types.ts` mirrors Pydantic response shapes.
- `src/lib/api/stream.ts` streams turn SSE and falls back to polling `/chats/turns/{turnId}`.

Chat store:

- `src/lib/store.tsx` loads chats, projects, and model sets after authentication.
- It keeps active chat/model-set ids in React state.
- It maps API snake_case into frontend camelCase model-set/chat/project types.

Turn state:

- The backend persists turns, model answers, verdicts, lessons, and costs. Legacy insurance rows may still exist and serialize safely.
- The frontend also keeps an in-memory running-turn cache in `src/lib/turnRunner.ts`.
- `resumeRunningTurns` restarts streaming/polling for pending or running turns when the route reloads.

Backend data:

- Main ORM models are `User`, `Organization`, `OrgMembership`, `OrgModel`, `UserPreferences`, `UserBrain`, `Project`, `Chat`, `ModelSet`, `Template`, `Turn`, `ModelAnswer`, `Verdict`, `VerdictLesson`, `DecisionInsurance`, `CostRecord`, `ShareLink`, and `AuditLog`.
- Services own business behavior; route modules are thin wrappers.
- Pydantic schemas define the API contract.

## 9. Environment/configuration

From `.env.example`:

- `VITE_API_URL`: frontend API base at build/runtime from the browser perspective. Defaults to `/api/v1`.
- `BACKEND_PROXY_TARGET`: dev and production web-server proxy target for `/api/v1`.
- `ENVIRONMENT`: backend environment, one of development/staging/production.
- `DEBUG`: backend debug flag.
- `SECRET_KEY`: JWT signing secret. Must be changed in production.
- `DATABASE_URL`: async SQLAlchemy database URL. Defaults to local SQLite; Docker/Render use PostgreSQL.
- `REDIS_URL`: Redis connection URL. Present for queue/runtime support, though current inspected turn streaming runs in-process.
- `CORS_ORIGINS`: comma-separated allowed frontend origins.
- `PUBLIC_APP_URL`: base public app URL, used for share links and OpenRouter referer fallback.
- `OPENROUTER_API_KEY`: required for real LLM calls through OpenRouter.
- `OPENROUTER_SITE_URL`: optional OpenRouter HTTP referer.
- `OPENROUTER_APP_NAME`: app name sent to OpenRouter.

Additional env vars seen in code/deployment:

- `API_PUBLIC_URL`: used by the frontend root route to emit a public `api-base` meta tag.
- `PORT`: used by Docker entrypoints/web server.
- `HOST`: used by frontend server startup.
- `NODE_ENV`: set to production in the frontend Docker runtime.

No real `.env` secret values were inspected or documented here.

## 10. Likely files for upcoming changes

Support 5 AIs: ChatGPT, Claude, Gemini, Grok, DeepSeek:

- `src/lib/modelIds.ts`: `MAX_COUNCIL_MODELS` controls the frontend council size.
- `src/lib/models.tsx`: `FLAGSHIP_MODEL_IDS` controls the first-class/default frontend model list.
- `backend/app/llm/catalog.py`: built-in model catalog, including Grok/xAI and DeepSeek.
- `backend/scripts/seed.py`: seeded system model sets.
- `src/components/chat/CouncilPickerModal.tsx`: slot layout/copy uses `MAX_COUNCIL_MODELS`.
- `src/components/ModelSetModal.tsx`: same limit and copy; layout should be checked for five.
- `src/routes/chat.tsx`: answer grid layout.
- `src/routes/shared.$token.tsx`: shared answer grid layout.

Focus UI on final verdict instead of showing every individual response:

- `src/routes/chat.tsx`: `AiTurn` currently renders every model answer card before the verdict.
- `src/routes/shared.$token.tsx`: public shared chat also renders every individual answer.
- `src/components/chat/MessageContent.tsx`: likely reusable for verdict display.
- `src/lib/api/types.ts` and backend schemas probably can keep model answers for audit/debug even if hidden by default.

Send each new user question together with the previous verdict:

- `src/routes/chat.tsx`: `send()` currently posts only `user_message`, model set, and custom instructions.
- `backend/app/services/chat_service.py`: best place to attach prior verdict context server-side because it can query previous turns reliably.
- `backend/app/llm/prompt_engine.py`: already has unused `chat_history` support for model answer prompts, but orchestrator does not currently pass history.
- `backend/app/prompts/system/model_answer.j2` and `backend/app/prompts/system/verdict.j2`: likely need explicit prior-verdict wording.
- `backend/app/llm/orchestrator.py`: `TurnContext` may need prior verdict/context fields.

Decision insurance has already been disabled:

- `backend/app/llm/orchestrator.py`: active Phase 3 was removed.
- `backend/app/llm/prompt_engine.py` and `backend/app/prompts/system/decision_insurance.j2`: prompt code/template remain unused legacy assets.
- `backend/app/db/models.py`: `DecisionInsurance`, `Turn.decision_insurance_enabled`, and `UsageKind.INSURANCE` remain schema-level legacy items. Removing them fully needs migrations.
- `backend/app/schemas/api.py` and `src/lib/api/types.ts`: response/request fields remain optional/backward-compatible.
- `src/routes/chat.tsx` and `src/routes/shared.$token.tsx`: no longer render insurance sections.
- `backend/app/services/chat_service.py`: no longer forces insurance enabled.

Rename "Disagree" to "Challenge":

- `src/routes/chat.tsx`: button text `I disagree`.
- `src/components/chat/VerdictDisagreeModal.tsx`: modal title/body/field wording.
- `src/routes/lessons.tsx`: labels such as "Disagreement lesson" and empty-state text.
- `src/routes/lessons.$id.tsx`: "Why you disagreed" and related labels.
- Backend names can remain internal initially, but API route `/lessons/turns/{turnId}/disagree` and schema `VerdictDisagreeRequest` would still expose old wording unless renamed.

Make Challenge use the same multi-AI flow as normal chat, not only the verdict AI:

- `src/components/chat/VerdictDisagreeModal.tsx`: currently submits lesson data directly.
- `src/routes/chat.tsx`: challenge action currently calls `api.lessons.disagree`; it does not create a new multi-model turn.
- `backend/app/api/v1/lessons.py` and `backend/app/services/lesson_service.py`: current challenge/lesson path uses only the verdict model for lesson generation.
- `backend/app/services/chat_service.py` and `backend/app/llm/orchestrator.py`: likely need a challenge turn type or custom instructions that include the user's challenge plus previous verdict.
- `backend/app/prompts/system/verdict_lesson.j2`: lesson generation may need to consume the challenge turn's multi-model answers.

When clicking "Finish and Build Lesson," build final lesson around Chafic's persona:

- There is no current button literally named "Finish and Build Lesson"; current modal submit text is `Build lesson`.
- `src/components/chat/VerdictDisagreeModal.tsx`: likely renamed/extended UI.
- `backend/app/services/lesson_service.py`: currently builds lessons as user vs verdict model, using `auth.user.full_name`.
- `backend/app/services/brain_service.py`: Chafic/persona context lives here and can be fed into lesson prompts.
- `backend/app/prompts/system/verdict_lesson.j2` and `backend/app/prompts/system/brain_update.j2`: likely prompt changes.

Keep Chafic as winning/main persona even if other AIs disagreed:

- `backend/app/services/lesson_service.py`: currently frames comparison as user vs model and stores `user_name`.
- `backend/app/services/brain_service.py`: currently tracks the authenticated user's brain, not a hardcoded persona winner.
- `backend/app/prompts/system/verdict.j2`, `model_answer.j2`, and `verdict_lesson.j2`: need stronger persona priority rules if Chafic should be the main/winning viewpoint.
- `backend/scripts/seed.py`: demo user's full name is already `Chafic`.

Persona builder improves/sharpens Chafic day by day from daily usage, challenges, verdicts, and lessons:

- `backend/app/db/models.py`: `UserBrain` currently stores aggregate profile and memories but no daily rollups.
- `backend/app/services/brain_service.py`: currently updates only from completed lessons, not all daily usage/chats/verdicts.
- `backend/app/services/chat_service.py`: can provide daily turn/verdict inputs to brain updates.
- `backend/app/services/lesson_service.py`: already invokes brain learning from lessons.
- `backend/app/prompts/system/brain_update.j2`: prompt would need broader inputs.
- Alembic migrations may be needed if daily snapshots, scoring, or source attribution become persistent requirements.

## 11. Risks/questions

- `src/routeTree.gen.ts` is generated and should not be manually edited.
- The worktree is currently broadly modified before this document was added, so existing changes should be treated carefully.
- Decision insurance is disabled in orchestration and UI, but legacy ORM/schema/API references remain. A future full cleanup would require migration planning and API compatibility decisions.
- The frontend council limit and backend model-set schema should stay aligned.
- The app supports dynamic OpenRouter models in addition to built-ins.
- Challenge flow is currently a lesson-generation path, not a normal multi-model turn. Making it use the full council will require a product decision: should Challenge create a new chat turn, a linked challenge turn, or a lesson draft after a challenge turn completes?
- Previous verdict context is not automatically included in new turns. There is reference-chat support in the composer, and prompt engine has a `chat_history` parameter, but the active normal chat flow does not inject previous verdicts.
- Brain/persona learning currently happens only from completed disagreement lessons. It does not learn day by day from every usage/challenge/verdict unless new backend logic is added.
- `BrainService.DEFAULT_BRAIN_MODEL` is hardcoded to `gpt-4.1`.
- The backend imports Redis/arq and defines `REDIS_URL`, but inspected turn execution currently runs in-process via SSE task creation.
- `ModelSetService.update` only updates `template_name` and `custom_instructions` when provided as non-`None`, which may make clearing those fields difficult.
- The README mentions `/api/v1/auth/signup`, but the inspected frontend login flow appears sign-in focused; verify auth route support before relying on signup UX.
- `package-lock.json` and `bun.lock` both exist. Docker uses npm, so npm should be the source of truth unless the team chooses Bun intentionally.
