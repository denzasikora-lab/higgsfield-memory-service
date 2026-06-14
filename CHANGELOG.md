# Changelog

## v0.1.0 - Contract scaffold

**What changed:** Added the FastAPI project scaffold, Docker Compose, Postgres +
pgvector schema, Alembic migrations, optional Bearer auth, raw turn persistence,
fixtures, and passing scaffold tests.

**Why:** The first milestone is to make the repository deployable through Docker
and establish the challenge HTTP contract before implementing full memory
extraction.

**Next:** Add deterministic structured memory extraction, fact evolution, and
hybrid recall ranking.

## v1.0.0 - Structured memory service

**What changed:** Implemented deterministic extraction for facts, preferences,
opinions, and events; added structured memory persistence, canonical-key
supersession, scoped search, prompt-ready recall, recall citations, and
token-budgeted context assembly.

**Why:** The challenge evaluates memory systems, not raw chat logs. The service
now stores transparent structured memories with provenance and returns relevant
agent-ready context.

**Result:** `/turns` synchronously extracts memories, `/recall` returns current
facts with historical context when available, `/search` returns structured memory
results, and user/session delete endpoints remove scoped data.

## v1.0.1 - Review polish

**What changed:** Made `/turns` persistence more atomic, committed repeated
same-value memory updates correctly, improved natural phrasing extraction for
relocation and job-title cases, replaced placeholder recency scoring, and added a
manual verification walkthrough.

**Why:** These changes make the code easier to review and reduce edge-case
surprises during private evaluation.

## v1.1.0 - Dynamic semantic memory

**What changed:** Added spaCy-first extraction with OpenAI structured JSON
fallback, OpenAI embeddings stored in `pgvector`, cosine-first recall/search,
metadata-driven display labels, and configurable scoped memory eviction.

**Why:** The service now generalizes beyond hardcoded extractors and lexical
overlap while still keeping deterministic fallbacks for local/no-secret tests.
