# Model set create/update diagnostics

Use this checklist when `POST /api/v1/model-sets` or `PATCH /api/v1/model-sets/{slug}` returns HTTP 500 in production but works locally.

## 1. Alembic revision drift

On the **deployed API host** (or against the production `DATABASE_URL`):

```bash
cd backend
alembic heads          # expected tip includes 039
alembic current        # must equal heads after deploy_bootstrap
```

If `current` lags `heads`, run the normal deploy bootstrap (preferred) or:

```bash
alembic upgrade head
python -m scripts.seed
```

Log confirmation for missing column width: Postgres error containing `value too long for type character varying(64)` on `verdict_model`.

## 2. Deployed commit hashes

Compare frontend and backend images/services:

```bash
# Render / host env — exact names vary by platform
echo "$RENDER_GIT_COMMIT"
echo "$SOURCE_VERSION"

# Or from the built artifact
git rev-parse HEAD
git rev-parse origin/main
```

Frontend and API should be from the same release train so `ModelSetCreateRequest` fields match what `src/lib/store.tsx` sends (`name`, `description`, `models`, `verdict_model`, `strategy`, `best_for`, `template_name`, `custom_instructions`).

## 3. Required environment variables

| Variable | Role |
|----------|------|
| `DATABASE_URL` | Postgres in production (`postgresql+asyncpg://…`) |
| `SECRET_KEY` | JWT auth |
| `ENVIRONMENT` | `production` disables open docs |
| `CORS_ORIGINS` / `PUBLIC_APP_URL` | Browser calls from the web app |
| `OPENROUTER_API_KEY` | Not required for create/update of sets (catalog ids only) |

Missing `DATABASE_URL` or wrong credentials surface as connection errors in API logs, not as successful list + failed write.

## 4. Database privileges on `model_sets`

As a DB admin (read-only diagnostic — do not insert test rows unless intended):

```sql
SELECT grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_name = 'model_sets'
ORDER BY grantee, privilege_type;

SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_name = 'model_sets'
ORDER BY ordinal_position;
```

The app role needs `SELECT`, `INSERT`, `UPDATE`, `DELETE` on `model_sets`.  
Confirm `verdict_model` is `character varying(128)` after migration `039`.

Log confirmation for missing grants: `model_set_write_failed` with `exception_message` containing `permission denied` / `insufficient privilege`, mapped to HTTP 500 `INTERNAL_ERROR`.

## 5. Application log signatures

Structured log event: **`model_set_write_failed`**

| Suspected cause | Confirming log fields |
|-----------------|------------------------|
| Overlong `verdict_model` (pre-039) | `exception_class` `DataError` / asyncpg truncation; message `varying(64)` |
| Overlong `best_for` / description copy | Same truncation on `best_for` / `varying(512)` |
| Invalid / empty model id | `exception_class` `ValidationError`; HTTP 400 |
| Editing system set | `exception_class` `ForbiddenError`; HTTP 403 |
| Missing slug | `exception_class` `NotFoundError`; HTTP 404 |
| Org FK / unique conflict | `exception_class` `IntegrityError` → HTTP 409 `ConflictError` |
| Auth missing | HTTP 401 before service (`UnauthorizedError`) |
| Wrong org header | HTTP 403 `ForbiddenError` |

Logs include `operation`, `user_id`, `org_id`, `model_set_slug`, `submitted_fields`, `exception_class`, `exception_message` (sanitized). They **do not** include tokens, API keys, or `custom_instructions` bodies.

## 6. Frontend payload (current contract)

`api.modelSets.create` / `update` send JSON:

```json
{
  "name": "…",
  "description": "…",
  "models": ["gpt-4.1", "or:openai--gpt-5.5"],
  "verdict_model": "or:openai--gpt-5.5",
  "strategy": "Synthesize",
  "best_for": "…",
  "template_name": "…",
  "custom_instructions": "…"
}
```

Headers: `Authorization: Bearer …`, `X-Org-Id: <org uuid>`.

Note: the modal sets `best_for` equal to the description string; descriptions longer than 512 characters are truncated into `best_for` server-side after this fix.
