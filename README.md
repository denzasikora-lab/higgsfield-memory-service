# Higgsfield Memory Service

Dockerized AI-agent memory service for the Higgsfield engineering challenge. It
accepts completed conversation turns, stores raw turns, extracts structured
memories, tracks fact evolution, and returns prompt-ready recall context.

## Architecture

```text
          POST /turns
              |
              v
       Request Validation
              |
              v
       Raw Turn Storage
              |
              v
 Deterministic Extraction Pipeline
              |
              v
 Structured Memory Store + Provenance
              |
              v
  Supersession + Search/Recall Index
              |
              v
 POST /recall -> Hybrid Ranking -> Token-budgeted context
```

The service keeps raw turn storage, extraction, persistence, and retrieval as
separate layers. `/turns` is synchronous: after it returns `201`, extracted
memories are immediately available through `/recall`, `/search`, and
`/users/{user_id}/memories`.

## Backing Store Choice

I chose PostgreSQL with `pgvector` because it gives the challenge a production-
leaning memory store while still running locally through Docker Compose. The
schema stores raw turns, structured memories, provenance, confidence, active
state, `supersedes`, a full-text `search_vector`, and a nullable
`embedding vector(1536)` column for future semantic retrieval.

Docker Compose uses a named Postgres volume:

```bash
docker compose down && docker compose up
```

Data survives that restart because the volume is not removed unless `-v` is
used.

## API Contract

- `GET /health`
- `POST /turns`
- `POST /recall`
- `POST /search`
- `GET /users/{user_id}/memories`
- `DELETE /sessions/{session_id}`
- `DELETE /users/{user_id}`

If `MEMORY_AUTH_TOKEN` is empty, auth is disabled. If it is set, memory endpoints
require `Authorization: Bearer <token>`. `GET /health` stays public.

## Extraction Pipeline

The default extraction path is deterministic and does not require an API key. It
extracts common challenge memories:

- facts: current city, previous city, employer, job title, pets, allergies,
  current projects
- preferences: answer style, diet, programming language preferences
- opinions: explanation style and evolving tool/language opinions
- events: relocation and tool-derived appointments

Every memory stores `type`, `key`, `value`, `confidence`, `source_turn`,
`source_session`, timestamps, `active`, and optional `supersedes`.

`OPENAI_API_KEY`, `OPENAI_MODEL`, and `USE_LLM_EXTRACTION` are documented for a
future optional LLM extractor, but the submitted default path is deterministic so
private eval can run without secrets.

## Recall Strategy

`/recall` loads user-scoped memories when `user_id` is present and falls back to
session-scoped memories when it is not. It ranks candidates with lexical overlap,
query-intent key matching, confidence, active status, and simple exact-value
boosts. The final context is assembled under `max_tokens` using `1 token ~= 4
characters`.

Prompt context is grouped as:

```md
## Known facts about this user
- Currently lives in Berlin (updated 2025-03-15; previously NYC)

## Preferences and opinions
- Prefers concise, direct answers

## Relevant recent context
- Moved to Berlin from NYC
```

If no memory is relevant, `/recall` returns:

```json
{"context": "", "citations": []}
```

## Fact Evolution

Hard facts and stable preferences use canonical keys. When a new active memory
arrives with the same canonical key and scope, the older active memory is marked
`active = false`, and the new memory points to it through `supersedes`.

Examples of superseded keys include `current_city`, `employer`, `job_title`,
`diet`, `allergy`, `pet`, `answer_style`, and `current_project`. Opinions evolve
more softly: same-key opinions are merged into a new active summary while older
opinion memories remain as history.

Scoping rules:

- memories with a `user_id` are shared across that user's sessions
- memories without a `user_id` are session-only
- different users and anonymous sessions do not mix

## Configuration

```env
APP_PORT=8080
DATABASE_URL=postgresql+asyncpg://memory:memory@postgres:5432/memory
MEMORY_AUTH_TOKEN=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
USE_LLM_EXTRACTION=false
LOG_LEVEL=INFO
```

## Run

```bash
docker compose up
```

The service will be available at:

```text
http://localhost:8080
```

Run migrations manually when developing outside Docker:

```bash
uv run alembic upgrade head
```

Run the app locally:

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 8080
```

## Tests

```bash
uv run ruff check .
uv run pytest
```

Docker smoke check:

```bash
docker compose up -d --build
curl -s http://localhost:8080/health
docker compose run --rm memory-service pytest
```

Manual memory check:

```bash
curl -s -X DELETE http://localhost:8080/users/demo-user

curl -s -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  --data '{"session_id":"demo-s1","user_id":"demo-user","messages":[{"role":"user","content":"I live in NYC and work at Stripe."}],"timestamp":"2025-03-01T10:00:00Z","metadata":{}}'

curl -s -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  --data '{"session_id":"demo-s2","user_id":"demo-user","messages":[{"role":"user","content":"I moved from NYC to Berlin last month. I joined Notion as a PM."}],"timestamp":"2025-03-15T10:00:00Z","metadata":{}}'

curl -s -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  --data '{"query":"Where does this user work now?","session_id":"demo-s2","user_id":"demo-user","max_tokens":256}'

curl -s http://localhost:8080/users/demo-user/memories
```

Expected result: recall mentions Notion, user memories show Stripe/NYC as
inactive historical facts and Notion/Berlin as active facts with `supersedes`.

## Tradeoffs

I optimized for deterministic contract correctness, transparent structured
memories, local Docker reliability, and no secret-dependent default path. The
`pgvector` column is scaffolded, but v1 ranking is lexical/key-intent based
rather than embedding-based. A production version would add LLM-assisted
extraction, embeddings, background quality metrics, and richer conflict
resolution policies.

## Failure Modes

- malformed JSON and invalid schemas return 4xx through FastAPI/Pydantic
- missing API key has no effect because deterministic extraction is default
- oversized message content is rejected by schema limits
- no relevant memory returns empty recall context and no citations
- slow or unavailable Postgres fails synchronously instead of hiding eventual
  consistency behind a background queue
