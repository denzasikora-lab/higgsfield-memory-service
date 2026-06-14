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
 spaCy Extraction + OpenAI JSON Fallback
              |
              v
 Structured Memory Store + pgvector Embeddings
              |
              v
 Supersession + Scoped Memory Eviction
              |
              v
 POST /recall -> Cosine Ranking -> Token-budgeted context
```

The service keeps raw turn storage, extraction, persistence, and retrieval as
separate layers. `/turns` is synchronous: after it returns `201`, extracted
memories are immediately available through `/recall`, `/search`, and
`/users/{user_id}/memories`.

## Backing Store Choice

I chose PostgreSQL with `pgvector` because it gives the challenge a production-
leaning memory store while still running locally through Docker Compose. The
schema stores raw turns, structured memories, provenance, confidence, active
state, `supersedes`, a full-text `search_vector`, and an
`embedding vector(1536)` column used for semantic cosine retrieval.

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

The default extraction provider is `spacy_openai_fallback`. It first runs spaCy
to collect entity labels and typed facts that can be grounded in named entities.
When spaCy cannot produce a high-confidence typed memory, or when the message
looks like a preference, correction, opinion, or update, it falls back to OpenAI
structured JSON extraction through `OPENAI_API_KEY` and `OPENAI_BASE_URL`.

The deterministic extractor remains available as a last-resort local fallback
and for tests with `EXTRACTION_PROVIDER=deterministic`. The pipeline extracts:

- facts: current city, previous city, employer, job title, pets, allergies,
  current projects
- preferences: answer style, diet, programming language preferences
- opinions: explanation style and evolving tool/language opinions
- events: relocation and tool-derived appointments

Every memory stores `type`, `key`, `value`, `confidence`, `source_turn`,
`source_session`, timestamps, `active`, optional `supersedes`, metadata labels,
an optional `display_label`, and an embedding generated from the structured
memory text.

## Recall Strategy

`/recall` embeds the query, retrieves scoped memories from Postgres by pgvector
cosine distance, and applies small exact key/value boosts only as tie-breakers.
If embeddings are unavailable, it falls back to the older lexical/key ranking so
local deterministic tests can still run without secrets. The final context is
assembled under `max_tokens` using `1 token ~= 4 characters`.

Prompt context is grouped as:

```md
## Known facts about this user
- current_city: Berlin (updated 2025-03-15; previously NYC)

## Preferences and opinions
- answer_style: Prefers concise, direct answers

## Relevant recent context
- relocation: Moved to Berlin from NYC
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

## Memory Eviction

`MEMORY_MAX_PER_SCOPE` limits stored memory rows inside one owner scope. If
`user_id` is present, the scope is that user. If it is absent, the scope is the
anonymous `session_id`. When the limit is exceeded, the service physically
deletes excess memory rows but never deletes turns during eviction.

Eviction removes inactive or superseded rows first, then lower-confidence older
rows, then the oldest active rows only if the scope is still over the limit.

## Configuration

```env
APP_PORT=8080
DATABASE_URL=postgresql+asyncpg://memory:memory@postgres:5432/memory
MEMORY_AUTH_TOKEN=
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-4o-nano
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
EXTRACTION_PROVIDER=spacy_openai_fallback
SPACY_MODEL=en_core_web_sm
USE_LLM_EXTRACTION=false
MEMORY_MAX_PER_SCOPE=200
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

I optimized for semantic retrieval, structured memory quality, and still having
a private local fallback path. spaCy gives cheap entity labels and OpenAI handles
preference/opinion/correction cases that are brittle with rules. Retrieval is
vector-first through pgvector cosine search, with lexical ranking retained only
as a degraded mode when embeddings cannot be generated.

## TODO

- Model multi-step location history more explicitly instead of relying on a
  single `previous_city` memory. The current implementation does not yet keep a
  clean ordered chain such as `previous_city_1`, `previous_city_2`, and so on.

## Failure Modes

- malformed JSON and invalid schemas return 4xx through FastAPI/Pydantic
- missing API key disables OpenAI extraction and embeddings; deterministic and
  lexical fallbacks keep local tests functional
- oversized message content is rejected by schema limits
- no relevant memory returns empty recall context and no citations
- slow or unavailable Postgres fails synchronously instead of hiding eventual
  consistency behind a background queue
