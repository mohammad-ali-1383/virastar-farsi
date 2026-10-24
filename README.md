# ویراستار متن فارسی — Persian Writing Assistant

AI-powered Persian proofreading service (spelling, grammar, punctuation, spacing, and
typography) that preserves the author's meaning and tone.

**Current status:** Phases 3 and 4 — the backend exposes `POST /api/v1/correct` (validation and the
documented response/error formats) behind two provider adapters, **Groq** as the primary and
**Google Gemini** as the automatic fallback, and the Persian RTL website calls that endpoint. See
`context/current-task.md`.

## Project structure

```
.
├── backend/                 # Python 3.12+ / FastAPI service
│   ├── app/
│   │   ├── config.py        # environment-driven settings
│   │   ├── errors.py        # error codes, exceptions, FastAPI handlers
│   │   ├── schemas.py       # request/response models
│   │   ├── main.py          # FastAPI app (health + correction router)
│   │   ├── routers/         # HTTP endpoints
│   │   └── services/        # correction service, prompt, and the Groq/Gemini adapters
│   ├── tests/               # pytest suite (provider calls are stubbed)
│   ├── Dockerfile
│   ├── requirements.txt     # runtime dependencies (pinned)
│   └── requirements-dev.txt # test dependencies (pinned)
├── frontend/                # React + TypeScript + Vite app
│   ├── src/
│   ├── .env.example         # frontend-only template (VITE_API_BASE_URL)
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml       # local development stack
├── .env.example             # sanitized environment template
└── context/                 # project specifications
```

## Prerequisites

- Node.js 20.19+ or 22.12+ (Node 24 recommended)
- Python 3.12+
- Docker with the Compose plugin (optional, for containerized development)

## Setup

Copy the environment template. `.env` is git-ignored — never commit real credentials.

```bash
cp .env.example .env
```

### Environment file locations

A single `.env` at the **repository root** configures the backend in both workflows below. The
loader resolves its env files relative to the backend package, not the current directory, so
`cd backend && uvicorn ...` reads the same root file that Docker Compose does. `backend/.env`
is still honored and overrides the root file if you prefer to keep a local copy.

| Workflow | `.env` location | How it is loaded |
|---|---|---|
| Docker Compose (backend) | `<repo root>/.env` | `docker-compose.yml` → `env_file: .env` |
| Local Uvicorn (`cd backend`) | `<repo root>/.env` (or `backend/.env`) | `backend/app/config.py` reads both |
| Local Vite (`cd frontend`) | `frontend/.env` (from `frontend/.env.example`) | Vite loads `VITE_*` at dev-server start |

Real environment variables always win over `.env` values, so a platform-provided secret (for
example in production) is never overridden by a stray file. Docker Compose overrides the backend
environment only for the container-specific log path
(`CORRECTION_LOG_PATH=/app/logs/corrections.jsonl`, backed by the `./logs` mount), and sets the
frontend's `VITE_API_BASE_URL`; both are documented in `docker-compose.yml`. Every other setting
in the table below comes from the root `.env` in both backend workflows, so `MAX_TEXT_LENGTH`,
`CORS_ORIGINS`, and the provider settings behave the same in Compose and in a local Uvicorn run.
In the Compose workflow the injected `VITE_API_BASE_URL` wins over `frontend/.env`, because Vite
gives real environment variables precedence.

### Option A — Docker Compose (recommended)

```bash
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

### Option B — run services locally

Backend:

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash); use .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

Frontend (in a second terminal):

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## API

`POST /api/v1/correct` accepts `{"text": "...", "store_for_evaluation": false}` and returns
`{"corrected_text": "..."}`. Every error uses `{"error": "CODE", "message": "..."}` with the codes
and statuses listed in `context/api-contract.md`: validation is `400`, unknown routes and methods
are `404 NOT_FOUND` / `405 METHOD_NOT_ALLOWED`, unexpected failures are `500 INTERNAL_ERROR`, and
**every** provider failure — rate limit, authentication, timeout, unavailable, or unusable
response — is the single `503 PROVIDER_UNAVAILABLE` (never `429`, `502`, or `504`).

```bash
curl -X POST http://localhost:8000/api/v1/correct \
  -H "Content-Type: application/json" \
  -d '{"text": "متن فارسی", "store_for_evaluation": false}'
```

`store_for_evaluation` is the consent flag for the optional evaluation log
(`context/correction-logging.md`): `true` allows storing the submitted and corrected text for
quality review, `false` or an omitted flag stores neither, and any non-boolean value is
`400 INVALID_INPUT`. The website asks for this choice in a Persian dialog when «اصلاح متن» is
pressed; dismissing the dialog sends no request. The backend, not the browser, decides whether
anything is written.

The text is validated in the backend; the maximum length comes from `MAX_TEXT_LENGTH`
(default `1000`). The backend sends the fixed proofreading prompt to the provider as its system
instruction, sends the user's text as a separate input, and returns the model's corrected text.

**Provider order:** Groq is always tried first. When it fails for a provider-related reason —
quota or rate limit, authentication/configuration error, timeout, connection failure, server
error, or an invalid/empty response — the same request is retried once with Gemini, and Gemini's
correction is returned. Invalid input and text over the length limit are rejected before either
provider is called. There is no other retry, and text is never fabricated: when both providers
fail the endpoint answers `503 PROVIDER_UNAVAILABLE` with a generic message. The browser never
learns which provider is active.

## Validation

```bash
# Backend
cd backend && pytest

# Frontend
cd frontend && npm run test       # Vitest + Testing Library component tests
cd frontend && npm run build      # TypeScript type check + production build
cd frontend && npm run typecheck
```

All tests mock the providers: no API key is needed and no request leaves the machine.

## Configuration

| Variable | Scope | Default | Purpose |
|---|---|---|---|
| `APP_ENV` | backend | `development` | Runtime environment label (read into settings; changes no behavior) |
| `MAX_TEXT_LENGTH` | backend | `1000` | Maximum accepted text length |
| `CORS_ORIGINS` | backend | dev origins | JSON array of allowed browser origins |
| `GROQ_API_KEY` | backend | *(empty)* | Groq API key (secret); Groq is always tried first |
| `GROQ_MODEL` | backend | `openai/gpt-oss-20b` | Groq model ID |
| `GROQ_TIMEOUT_SECONDS` | backend | `30` | Per-request Groq timeout |
| `GEMINI_API_KEY` | backend | *(empty)* | Gemini API key (secret); used when Groq fails |
| `GEMINI_MODEL` | backend | `gemini-3.5-flash-lite` | Gemini model ID |
| `GEMINI_TIMEOUT_SECONDS` | backend | `30` | Per-request Gemini timeout |
| `CORRECTION_LOG_PATH` | backend | `<repo root>/logs/corrections.jsonl` | Consented evaluation log (JSON Lines) |
| `VITE_API_BASE_URL` | frontend | `http://localhost:8000` | Backend base URL used by the browser |

`MAX_TEXT_LENGTH` is enforced by the backend only. The website keeps its own fixed
1000-character counter (`frontend/src/api.ts`), so raising the backend limit allows longer API
requests while the page still blocks input at 1000 characters; the server remains the authority.

Secrets live only in backend environment variables. Never place credentials in frontend
code or in `.env` files that are committed. Each key is sent to its own provider in a request header
— Groq in `Authorization: Bearer`, Gemini in `x-goog-api-key` (never the URL) — and is never
logged or returned. Only the key of the provider being called is sent; the other key is never
used. With the fallback in place, set both keys so a Groq outage does not become a service outage.

## Evaluation log (optional)

Nothing is stored unless a request explicitly consents (`store_for_evaluation: true`). With consent,
the backend appends one JSON Lines row per successful correction to `CORRECTION_LOG_PATH`
(default `<repo root>/logs/corrections.jsonl`):

```json
{"timestamp":"2026-10-16T12:30:00+00:00","original_text":"...","provider":"gemini","model":"gemini-3.5-flash-lite","corrected_text":"..."}
```

`provider`/`model` name the provider that **actually succeeded**, so a Groq-to-Gemini fallback is
recorded as Gemini. Declined requests store nothing and do not even create the file, and a failed
write never breaks or retries the request — it returns the correction and logs a sanitized warning.
`logs/` is git-ignored. Docker Compose sets `CORRECTION_LOG_PATH=/app/logs/corrections.jsonl` and
mounts `./logs`, so records survive a container recreate; delete `logs/corrections.jsonl` to clear
them.

## Local integration check (live provider)

The automated tests mock both providers, need no key, and never make a real API call. No live
connection is verified until an actual API request succeeds.

1. Get a key:
   - Groq — https://console.groq.com/keys (primary)
   - Google Gemini — https://aistudio.google.com/apikey (fallback)
2. Put them in `<repo root>/.env` (git-ignored) — never in source, chat, or a committed file:

   ```bash
   # Groq is always tried first.
   GROQ_API_KEY=your-key-here
   GROQ_MODEL=openai/gpt-oss-20b

   # Gemini is used automatically when Groq fails. With GEMINI_API_KEY empty, a
   # Groq failure has no fallback and the request returns 503 PROVIDER_UNAVAILABLE.
   GEMINI_API_KEY=your-key-here
   GEMINI_MODEL=gemini-3.5-flash-lite
   ```

3. Start the backend — pick one workflow:

   Local Uvicorn:

   ```bash
   cd backend && uvicorn app.main:app --reload --port 8000
   ```

   Docker Compose (from the repository root). A changed `.env` is only picked up when the
   container is recreated, so use `up` rather than `restart`; `--force-recreate` guarantees it.
   No image rebuild is needed for a `.env` edit, and none is needed for application code
   changes either, because `./backend/app` is bind-mounted with `--reload`:

   ```bash
   docker compose up -d --force-recreate backend
   # add --build only if backend/requirements*.txt or the Dockerfile changed
   ```

4. Send a real request and check the result:

   ```bash
   curl -X POST http://localhost:8000/api/v1/correct \
     -H "Content-Type: application/json" \
     -d '{"text": "سلام این یک متن ازمایشی است"}'
   ```

   A corrected `{"corrected_text": "..."}` means the working key, model, and endpoint produced a
   correction. Which provider answered is visible in the backend log, which names the providers and
   the sanitized error codes only ("Provider groq failed (code=PROVIDER_RATE_LIMIT), falling back to
   gemini"). `503 PROVIDER_UNAVAILABLE` means Groq failed and Gemini failed too (or is not
   configured) — the endpoint never returns the text unchanged in that case.

   To force the fallback deliberately, temporarily set a wrong `GROQ_API_KEY` (or an empty one) and
   check that the request still returns a correction and that the log shows the Groq failure.

   Confirm the loaded configuration without exposing the keys:

   ```bash
   # Local
   cd backend && python -c "from app.config import settings; print(settings.groq_model, settings.gemini_model, bool(settings.groq_api_key.get_secret_value()), bool(settings.gemini_api_key.get_secret_value()))"
   # Compose
   docker compose exec backend python -c "from app.config import settings; print(settings.groq_model, settings.gemini_model, bool(settings.groq_api_key.get_secret_value()), bool(settings.gemini_api_key.get_secret_value()))"
   ```

   Both print the two model IDs and `True` per configured key (never a key itself).

## Roadmap

Phase 2 adds `POST /api/v1/correct`, Phase 3 the Groq and Gemini provider adapters with the automatic
Groq-to-Gemini fallback, and Phase 4 the website. V2/V3 add further providers and a Telegram bot.
See `context/task-plan.md`.
