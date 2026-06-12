# Higgsfield AI Engineering Challenge - подробное описание задания

## Рекомендуемое название репозитория

Лучшее название:

`higgsfield-memory-service`

Почему так:

- сразу понятно, что это под Higgsfield
- сразу понятно, что это memory service
- выглядит профессионально для GitHub и Typeform
- не слишком длинное
- не выглядит как временная папка или тестовый проект

Альтернативы:

- `ai-agent-memory-service`
- `structured-agent-memory`
- `agent-memory-service`
- `memory-service-engineering-challenge`

Я бы выбрал именно:

`higgsfield-memory-service`

---

# 1. Общая идея задания

Нужно сделать сервис памяти для AI-агента.

Это не обычный чат-лог и не просто база сообщений.

Сервис должен принимать завершенные куски диалога, извлекать из них полезные знания о пользователе, сохранять эти знания в структурированном виде и потом отдавать релевантный контекст агенту, когда он задает recall-запрос.

Главная цель - показать, что ты умеешь проектировать memory layer для AI-agent system.

Сервис должен уметь:

- принимать completed conversation turns
- сохранять raw turns
- извлекать structured memories
- различать факты, предпочтения, мнения, события
- обновлять старые факты при появлении новых
- сохранять историю изменений
- отдавать prompt-ready context через `/recall`
- отдавать structured search results через `/search`
- показывать все memories пользователя через `/users/{user_id}/memories`
- удалять данные сессии
- удалять данные пользователя
- переживать рестарт Docker-контейнера
- проходить contract tests
- иметь self-eval fixture для проверки качества recall

---

# 2. Что нужно сдать

Нужно сдать GitHub-репозиторий.

В репозитории должен быть Dockerized service, который запускается командой:

```bash
docker compose up
```

После запуска сервис должен быть доступен на:

```text
http://localhost:8080
```

Нельзя требовать ручной установки зависимостей типа:

```bash
pip install ...
npm install ...
python setup.py ...
```

Все должно подниматься через Docker.

---

# 3. Ожидаемая структура репозитория

Рекомендуемая структура:

```text
higgsfield-memory-service/
├── README.md
├── CHANGELOG.md
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── src/
│   ├── main.py
│   ├── api/
│   ├── db/
│   ├── memory/
│   ├── retrieval/
│   └── schemas/
├── tests/
│   ├── test_contract.py
│   ├── test_persistence.py
│   ├── test_sessions.py
│   ├── test_malformed_input.py
│   └── test_recall_quality.py
└── fixtures/
    ├── recall_eval.json
    └── conversations.json
```

Можно сделать проще, но обязательно должны быть:

- `README.md`
- `CHANGELOG.md`
- `docker-compose.yml`
- `Dockerfile`
- `src/`
- `tests/`
- `fixtures/`
- `.env.example`

---

# 4. Что сервис должен делать

Сервис должен реализовать HTTP API.

Основные endpoints:

- `GET /health`
- `POST /turns`
- `POST /recall`
- `POST /search`
- `GET /users/{user_id}/memories`
- `DELETE /sessions/{session_id}`
- `DELETE /users/{user_id}`

---

# 5. Авторизация

Авторизация опциональна.

Если ты добавляешь авторизацию, она должна работать через Bearer token:

```http
Authorization: Bearer <token>
```

Если auth включен, токен будет передан через env:

```env
MEMORY_AUTH_TOKEN=your_token_here
```

Если не хочешь усложнять, можно не делать auth вообще.

---

# 6. Endpoint: GET /health

## Назначение

Проверяет, что сервис жив и готов принимать запросы.

## Request

```http
GET /health
```

## Expected response

```http
200 OK
```

Пример body:

```json
{
  "status": "ok"
}
```

Главное - вернуть status code `200`.

---

# 7. Endpoint: POST /turns

## Назначение

Сохраняет завершенный turn диалога.

Turn - это один завершенный кусок общения, который может содержать несколько сообщений:

- user message
- assistant message
- tool message
- несколько user/assistant/tool сообщений

## Что должен сделать сервис

После получения запроса сервис должен:

1. Валидировать input.
2. Сохранить raw turn.
3. Запустить extraction pipeline.
4. Извлечь structured memories.
5. Обновить существующие memories, если появились новые факты.
6. Пометить старые contradicted facts как superseded.
7. Проиндексировать memories для поиска.
8. Вернуть response только после завершения всех операций.

Важно:

После ответа от `/turns` данные должны сразу быть доступны через:

- `/recall`
- `/search`
- `/users/{user_id}/memories`

Нельзя делать eventual consistency.

То есть нельзя ответить `201`, а потом в фоне когда-нибудь извлечь memories.

## Timeout

Их eval ждет максимум:

```text
60 seconds
```

Поэтому `/turns` должен успевать завершиться за 60 секунд.

---

## Request schema

```json
{
  "session_id": "string",
  "user_id": "string | null",
  "messages": [
    {
      "role": "user",
      "content": "string"
    },
    {
      "role": "assistant",
      "content": "string"
    },
    {
      "role": "tool",
      "name": "string | null",
      "content": "string"
    }
  ],
  "timestamp": "ISO-8601 string",
  "metadata": {}
}
```

## Important details

`session_id` обязателен.

`user_id` может быть `null`.

`messages` - массив сообщений.

Каждое сообщение содержит:

- `role`
- `content`
- optional `name`, если это tool message

Допустимые роли:

- `user`
- `assistant`
- `tool`

`timestamp` - строка в ISO-8601 формате.

`metadata` - произвольный объект.

---

## Response

```http
201 Created
```

```json
{
  "id": "string"
}
```

`id` - это ID сохраненного turn.

---

# 8. Endpoint: POST /recall

## Назначение

Это главный endpoint.

Именно он, скорее всего, даст основной score на private eval.

`/recall` должен вернуть готовый контекст, который AI-агент вставит в prompt перед следующим ответом.

Это не просто search results.

Это аккуратно собранный текстовый context.

---

## Request schema

```json
{
  "query": "string",
  "session_id": "string",
  "user_id": "string | null",
  "max_tokens": 1024
}
```

## Fields

`query` - вопрос или задача, для которой нужно достать память.

Пример:

```text
Where does this user live?
```

`session_id` - текущая сессия.

`user_id` - пользователь, если известен.

`max_tokens` - лимит на размер context.

---

## Response schema

```json
{
  "context": "string",
  "citations": [
    {
      "turn_id": "string",
      "score": 0.0,
      "snippet": "string"
    }
  ]
}
```

## Fields

`context` - готовый текст для prompt агента.

`citations` - источники, откуда взята память.

Каждая citation содержит:

- `turn_id`
- `score`
- `snippet`

---

## Пример хорошего context

```md
## Known facts about this user
- Works at Notion as a PM (updated 2025-03-15; previously at Stripe as an engineer)
- Vegetarian, allergic to shellfish
- Has a dog named Biscuit
- Prefers concise, direct answers

## Relevant from recent conversations
- [2025-03-10] User was debugging a React performance issue with excessive re-renders in a dashboard component
- [2025-03-14] User mentioned preparing for a system design interview at a FAANG company
```

---

## Что важно для /recall

Сервис должен:

- вернуть не просто список chunks, а понятный context
- учитывать `query`
- учитывать `session_id`
- учитывать `user_id`
- не смешивать разных пользователей
- не смешивать разные сессии, если `user_id` разный
- уметь использовать user-level memories между сессиями одного пользователя
- соблюдать `max_tokens` хотя бы приблизительно
- при маленьком budget сначала включать самые важные stable facts
- потом query-relevant memories
- потом recent context
- не галлюцинировать, если данных нет

---

## Empty recall response

Если ничего не найдено, нужно вернуть:

```json
{
  "context": "",
  "citations": []
}
```

Не надо выдумывать факты.

Не надо возвращать generic text.

Не надо писать что-то типа:

```text
No relevant memory found.
```

Лучше именно пустой context.

---

# 9. Endpoint: POST /search

## Назначение

`/search` - это explicit search endpoint.

Его агент может вызвать как tool.

Отличие:

- `/recall` возвращает prompt-ready context
- `/search` возвращает structured results

---

## Request schema

```json
{
  "query": "string",
  "session_id": "string | null",
  "user_id": "string | null",
  "limit": 10
}
```

## Response schema

```json
{
  "results": [
    {
      "content": "string",
      "score": 0.0,
      "session_id": "string",
      "timestamp": "ISO-8601",
      "metadata": {}
    }
  ]
}
```

## Что важно

`/search` должен искать по memories, а не просто по raw messages.

Хорошо, если он умеет:

- exact keyword search
- semantic-ish search
- искать по key/value/type
- учитывать session/user scope
- сортировать по score
- ограничивать по `limit`

---

# 10. Endpoint: GET /users/{user_id}/memories

## Назначение

Возвращает все memories пользователя.

Этот endpoint нужен для debugging и inspection.

Они будут смотреть его руками.

---

## Request

```http
GET /users/{user_id}/memories
```

## Response schema

```json
{
  "memories": [
    {
      "id": "string",
      "type": "fact | preference | opinion | event",
      "key": "string",
      "value": "string",
      "confidence": 0.0,
      "source_session": "string",
      "source_turn": "string",
      "created_at": "ISO-8601",
      "updated_at": "ISO-8601",
      "supersedes": "string | null",
      "active": true
    }
  ]
}
```

## Что важно

Это должны быть structured memories.

Плохо:

```json
{
  "type": "chunk",
  "value": "User said: I moved to Berlin from NYC last month..."
}
```

Хорошо:

```json
{
  "type": "fact",
  "key": "current_city",
  "value": "Berlin",
  "confidence": 0.95,
  "source_session": "smoke-1",
  "source_turn": "turn_123",
  "supersedes": "memory_old_nyc",
  "active": true
}
```

Еще хорошо:

```json
{
  "type": "event",
  "key": "relocation",
  "value": "Moved to Berlin from NYC in February 2025",
  "confidence": 0.9,
  "source_session": "smoke-1",
  "source_turn": "turn_123",
  "active": true
}
```

---

# 11. Endpoint: DELETE /sessions/{session_id}

## Назначение

Удаляет все данные конкретной session.

## Request

```http
DELETE /sessions/{session_id}
```

## Response

```http
204 No Content
```

## Что удалить

Нужно удалить или деактивировать:

- turns этой session
- session-level memories этой session
- search index entries этой session

Важно:

Если memory стала user-level и была подтверждена в других session, можно сохранить ее. Но это нужно объяснить в README.

Для простого решения можно удалить все memories, у которых `source_session = session_id`.

---

# 12. Endpoint: DELETE /users/{user_id}

## Назначение

Удаляет все данные пользователя.

## Request

```http
DELETE /users/{user_id}
```

## Response

```http
204 No Content
```

## Что удалить

Нужно удалить:

- user memories
- turns пользователя
- sessions пользователя
- indexed data пользователя

---

# 13. Главные сложные части задания

## 13.1 Fact evolution / contradiction handling

Сервис должен понимать, что факты могут меняться.

Пример:

Пользователь говорит:

```text
I work at Stripe
```

Позже пользователь говорит:

```text
I just started at Notion
```

Оба факта относятся к одной теме:

```text
employment
```

Нужно понять, что новый факт заменяет старый.

Правильное поведение:

- старый факт остается в базе
- старый факт становится `active = false`
- новый факт становится `active = true`
- новый факт может ссылаться на старый через `supersedes`
- `/recall` возвращает новый актуальный факт
- `/users/{user_id}/memories` показывает историю

Пример memories:

```json
{
  "id": "mem_1",
  "type": "fact",
  "key": "employer",
  "value": "Stripe",
  "active": false,
  "supersedes": null
}
```

```json
{
  "id": "mem_2",
  "type": "fact",
  "key": "employer",
  "value": "Notion",
  "active": true,
  "supersedes": "mem_1"
}
```

---

## 13.2 Какие факты нужно уметь обновлять

Особенно важно обрабатывать изменения в таких темах:

- city
- country
- employer
- job title
- school
- relationship status
- family
- pets
- dietary preferences
- allergies
- programming language preferences
- tool preferences
- opinions
- current projects

---

## 13.3 Corrections

Нужно понимать исправления.

Пример:

```text
I live in Berlin
```

Потом:

```text
Actually, I meant Munich
```

Система должна понять:

- Berlin был ошибочным или устаревшим фактом
- Munich должен стать active current city

Пример:

```json
{
  "key": "current_city",
  "value": "Berlin",
  "active": false
}
```

```json
{
  "key": "current_city",
  "value": "Munich",
  "active": true,
  "supersedes": "previous_berlin_memory_id"
}
```

---

## 13.4 Soft opinion evolution

Мнения могут не заменяться полностью.

Пример:

```text
I love TypeScript
```

Потом:

```text
TypeScript generics are getting annoying
```

Потом:

```text
TypeScript is fine for big projects but I'd use Python for scripts
```

Это не простой overwrite.

Лучше хранить это как evolving opinion.

Например:

```json
{
  "type": "opinion",
  "key": "typescript",
  "value": "Likes TypeScript overall, but finds generics annoying and prefers Python for scripts",
  "confidence": 0.85,
  "active": true
}
```

А старые мнения можно оставить как inactive или historical.

В README нужно объяснить стратегию:

- hard facts overwrite by canonical key
- opinions are summarized over time
- contradictions reduce confidence or create evolved opinion memory
- newest explicit correction has priority

---

# 14. Extraction, not storage

Они отдельно подчеркивают:

Memory service - это не message log.

Нельзя просто сохранять raw messages и потом искать по ним.

Нужно извлекать знания.

---

## Минимум extraction должен находить

### Personal facts

Примеры:

```text
I work at Notion
```

Memory:

```json
{
  "type": "fact",
  "key": "employer",
  "value": "Notion"
}
```

```text
I live in Berlin
```

Memory:

```json
{
  "type": "fact",
  "key": "current_city",
  "value": "Berlin"
}
```

---

### Preferences

Пример:

```text
I prefer concise answers
```

Memory:

```json
{
  "type": "preference",
  "key": "answer_style",
  "value": "Prefers concise answers"
}
```

---

### Opinions

Пример:

```text
I hate overly abstract explanations
```

Memory:

```json
{
  "type": "opinion",
  "key": "explanation_style",
  "value": "Dislikes overly abstract explanations"
}
```

---

### Events

Пример:

```text
I moved to Berlin from NYC last month
```

Memory:

```json
{
  "type": "event",
  "key": "relocation",
  "value": "Moved to Berlin from NYC last month"
}
```

И также можно извлечь fact:

```json
{
  "type": "fact",
  "key": "current_city",
  "value": "Berlin"
}
```

---

### Implicit facts

Пример:

```text
I was walking Biscuit this morning
```

Нужно понять:

- Biscuit, вероятно, питомец пользователя
- пользователь имеет питомца по имени Biscuit

Memory:

```json
{
  "type": "fact",
  "key": "pet",
  "value": "Has a pet named Biscuit"
}
```

---

### Tool-derived facts

Если есть tool messages, можно извлечь факты из результата tool.

Пример tool message:

```json
{
  "role": "tool",
  "name": "calendar",
  "content": "User has a dentist appointment on Friday"
}
```

Memory:

```json
{
  "type": "event",
  "key": "appointment",
  "value": "Has a dentist appointment on Friday"
}
```

Но нужно быть осторожным с confidence.

---

# 15. Context assembly under max_tokens

`/recall` должен учитывать `max_tokens`.

Нужно приблизительно ограничивать размер context.

Точный tokenizer не обязателен, можно использовать approximation:

```text
1 token ≈ 4 characters
```

Пример:

```text
max_chars = max_tokens * 4
```

---

## Приоритеты context

При маленьком бюджете лучше включать в таком порядке:

1. Active stable facts about user
2. Query-relevant memories
3. Important preferences
4. Recent session context
5. Historical superseded facts only if relevant

---

## Пример хорошей стратегии

Если query:

```text
Where does this user live?
```

В context нужно включить:

```text
- Currently lives in Berlin
- Previously lived in NYC
```

Не нужно включать:

```text
- Likes TypeScript
- Has dog Biscuit
- Prefers concise answers
```

Если query:

```text
What should I know about how to answer this user?
```

В context нужно включить:

```text
- Prefers concise direct answers
- Dislikes overly abstract explanations
```

---

# 16. Persistence

Данные должны переживать рестарт.

Команда:

```bash
docker compose down && docker compose up
```

После этого данные должны остаться.

Важно:

`docker compose down` без `-v` не удаляет named volume.

Поэтому нужно использовать named volume.

Пример в `docker-compose.yml`:

```yaml
services:
  memory-service:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - memory_data:/app/data
    env_file:
      - .env

volumes:
  memory_data:
```

Если используешь SQLite, база может лежать здесь:

```text
/app/data/memory.db
```

---

# 17. Concurrent sessions

Разные `session_id` не должны смешиваться.

Пример:

User A:

```text
session_id = s1
user_id = user-a
I live in Berlin
```

User B:

```text
session_id = s2
user_id = user-b
I live in Tokyo
```

Recall для user-a не должен вернуть Tokyo.

Recall для user-b не должен вернуть Berlin.

---

## Cross-session user memory

Если `user_id` одинаковый, можно шарить memory между session.

Пример:

Session 1:

```text
user_id = user-1
I live in Berlin
```

Session 2:

```text
user_id = user-1
Where do I live?
```

Можно вернуть Berlin, даже если session_id другой.

Это хорошо.

Но нужно объяснить в README:

- session-level memories scoped by session
- user-level memories shared by user_id
- if user_id is null, fallback to session-only memory

---

# 18. Synchronous correctness

Это важное требование.

После успешного:

```http
POST /turns
```

Нужно сразу иметь возможность вызвать:

```http
POST /recall
```

И получить новые memories.

Также сразу должен работать:

```http
GET /users/{user_id}/memories
```

Нельзя:

- класть extraction в background queue
- возвращать 201 до завершения extraction
- говорить, что данные появятся позже

---

# 19. Recall latency

`POST /recall` должен быть быстрым.

Желательно:

```text
< 500 ms for small datasets
< 2 seconds for larger local eval
```

Если используешь LLM на recall, это может быть медленно.

Лучше:

- LLM использовать на extraction
- recall делать локально через DB + ranking
- context assembly делать без LLM

Так будет надежнее.

---

# 20. Resilience

Сервис не должен падать на плохом input.

Нужно обработать:

- malformed JSON
- missing fields
- wrong types
- empty messages
- huge content
- unicode oddities
- unknown role
- null user_id
- empty session

Плохой input должен возвращать 4xx.

Например:

```http
400 Bad Request
```

или

```http
422 Unprocessable Entity
```

---

# 21. LLM usage

LLM можно использовать.

Они даже поощряют.

Но нужно описать:

- какой model используется
- где используется
- зачем используется
- что будет, если ключа нет
- какие env vars нужны

---

## Хороший вариант

LLM используется только для extraction.

Например:

```text
POST /turns -> raw messages -> LLM extraction -> structured memories -> DB
```

А recall делается без LLM:

```text
POST /recall -> query -> search active memories -> rank -> assemble context
```

Это надежно и быстро.

---

## Если API key отсутствует

Нужно иметь fallback.

Например:

- regex/rule-based extractor
- simple keyword extraction
- deterministic extraction для common patterns

В README можно написать:

```text
If OPENAI_API_KEY is missing, the service falls back to a deterministic rule-based extractor.
```

---

# 22. .env.example

Файл `.env.example` должен содержать все переменные окружения.

Пример:

```env
APP_PORT=8080
DATABASE_URL=sqlite:////app/data/memory.db
MEMORY_AUTH_TOKEN=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
USE_LLM_EXTRACTION=false
LOG_LEVEL=INFO
```

Если не используешь OpenAI, можно убрать.

---

# 23. README.md - что обязательно написать

README должен объяснять проект так, чтобы reviewer понял дизайн за 5 минут.

Обязательные разделы:

## 23.1 Architecture

Нужна диаграмма.

Можно ASCII:

```text
          POST /turns
              |
              v
       Raw Turn Storage
              |
              v
      Extraction Pipeline
              |
              v
  Structured Memory Store
              |
              v
      Search / Recall Index
              |
              v
 POST /recall -> Context Assembly -> Prompt-ready context
```

И 1-2 абзаца объяснения.

---

## 23.2 Backing store choice

Нужно объяснить, что выбрал для хранения и почему.

Например:

```text
I chose SQLite with FTS5 because the challenge is single-service, Dockerized, local, and time-boxed. SQLite provides durable persistence through a Docker volume, simple deployment, transactions for synchronous correctness, and FTS5 for lexical retrieval. For a production version I would move to Postgres + pgvector.
```

---

## 23.3 Extraction pipeline

Нужно объяснить:

- как raw turns превращаются в memories
- какие types memories есть
- как работает confidence
- как работает provenance
- как работает fallback без LLM

---

## 23.4 Recall strategy

Нужно объяснить:

- как выбираются candidate memories
- как работает ranking
- как работает session/user scope
- как собирается final context
- как соблюдается max_tokens
- что происходит, если данных нет

---

## 23.5 Fact evolution

Нужно объяснить:

- как обрабатываются contradictions
- как обрабатываются corrections
- как работает supersedes
- какие keys считаются canonical
- как работает opinion evolution

---

## 23.6 Tradeoffs

Нужно честно написать:

- что оптимизировано
- чем пожертвовал
- что сделал бы следующим

Пример:

```text
I optimized for deterministic contract correctness, local persistence, and transparent structured memories. I intentionally avoided heavy online embedding dependencies in the default path to keep the service easy to run in private evaluation.
```

---

## 23.7 Failure modes

Нужно описать:

- no data
- missing API key
- malformed input
- slow disk
- oversized payload
- low confidence extraction
- LLM failure

---

## 23.8 How to run tests

Пример:

```bash
docker compose run --rm memory-service pytest
```

или:

```bash
pytest
```

Если тесты запускаются внутри Docker, лучше.

---

# 24. CHANGELOG.md

CHANGELOG очень важен.

Они прямо говорят, что хороший changelog может быть важнее, чем формально высокий score.

Нужно сделать 4+ entries.

Каждая entry должна объяснять:

- что изменилось
- почему изменилось
- какой результат
- что осталось плохо
- что дальше

---

## Пример CHANGELOG.md

```md
# Changelog

## v1 - Contract-compliant memory service

**What changed:** Implemented the required HTTP endpoints: `/health`, `/turns`, `/recall`, `/search`, `/users/{user_id}/memories`, session delete, and user delete.

**Why:** The first priority was to satisfy the challenge contract and make the service runnable through Docker.

**Result:** Basic smoke test passes. Turns are persisted and recall returns context from stored memories.

**Next:** Extraction is still mostly rule-based and needs better fact typing.

---

## v2 - Structured memory extraction

**What changed:** Added structured memory extraction with memory types: fact, preference, opinion, and event. Added canonical keys for location, employer, pets, allergies, and answer style.

**Why:** Raw message chunks are not enough for the challenge. The evaluator inspects `/users/{user_id}/memories` and expects structured knowledge.

**Result:** The service now extracts memories such as `current_city=Berlin`, `previous_city=NYC`, and `pet=Biscuit` from natural language turns.

**Next:** Contradiction handling needs to supersede older memories.

---

## v3 - Fact evolution and supersession

**What changed:** Added canonical-key conflict detection. New high-confidence facts with the same canonical key deactivate older facts and link to them through `supersedes`.

**Why:** The private eval includes fact evolution scenarios such as "I work at Stripe" followed by "I just joined Notion".

**Result:** `/recall` now returns the current active fact while `/users/{user_id}/memories` still exposes the historical superseded facts.

**Next:** Opinion evolution is still too simple and should preserve nuance instead of overwriting aggressively.

---

## v4 - Hybrid recall ranking

**What changed:** Added hybrid ranking using lexical FTS, key matching, recency, active-memory priority, and simple query-intent boosts.

**Why:** Pure recency or pure lexical matching misses multi-hop and indirect queries, while vector-only search can miss exact facts such as dog names or city names.

**Result:** Recall fixture score improved from 0.55 to 0.72 on scripted probes.

**Next:** Multi-hop recall still needs better relationship linking.

---

## v5 - Recall quality fixture and token-budgeted context

**What changed:** Added scripted recall fixture with conversations, probe queries, expected facts, and a simple quality metric. Added approximate token-budget enforcement in `/recall`.

**Why:** The challenge rewards candidates who measure recall quality and iterate based on failures.

**Result:** The service now reports X/Y expected facts found in context and avoids exceeding small `max_tokens` budgets.

**Next:** Add optional LLM-based extraction for harder implicit facts.
```

---

# 25. Tests

Нужно сделать tests.

Минимум 4 группы.

---

## 25.1 Contract roundtrip test

Проверяет:

1. POST `/turns`
2. Получили `201`
3. Получили `{ "id": "..." }`
4. POST `/recall`
5. Получили response с `context` и `citations`

Пример сценария:

```text
User says: I live in Berlin.
Query: Where does the user live?
Expected: context contains Berlin.
```

---

## 25.2 Restart persistence test

Проверяет:

1. Записали turn.
2. Перезапустили контейнер.
3. Вызвали recall.
4. Данные остались.

Важно:

Данные должны лежать в named Docker volume.

---

## 25.3 Concurrent sessions test

Проверяет:

User A:

```text
I live in Berlin
```

User B:

```text
I live in Tokyo
```

Expected:

- recall user A -> Berlin, not Tokyo
- recall user B -> Tokyo, not Berlin

---

## 25.4 Malformed input test

Проверяет:

- bad JSON
- missing session_id
- missing messages
- empty messages
- invalid role
- unicode weirdness
- oversized payload

Expected:

- 4xx
- service does not crash

---

## 25.5 Recall quality fixture

В `fixtures/` нужно положить scripted conversations.

Например:

```json
{
  "conversations": [
    {
      "name": "location_evolution",
      "turns": [
        {
          "session_id": "s1",
          "user_id": "u1",
          "messages": [
            {
              "role": "user",
              "content": "I live in NYC and work at Stripe."
            }
          ],
          "timestamp": "2025-03-01T10:00:00Z",
          "metadata": {}
        },
        {
          "session_id": "s2",
          "user_id": "u1",
          "messages": [
            {
              "role": "user",
              "content": "I just moved to Berlin and started at Notion."
            }
          ],
          "timestamp": "2025-03-15T10:00:00Z",
          "metadata": {}
        }
      ],
      "probes": [
        {
          "query": "Where does the user live now?",
          "expected": ["Berlin"]
        },
        {
          "query": "Where does the user work now?",
          "expected": ["Notion"]
        }
      ]
    }
  ]
}
```

Simple metric:

```text
score = matched_expected_facts / total_expected_facts
```

---

# 26. Smoke test from challenge

После запуска:

```bash
docker compose up
```

Проверить health:

```bash
curl -s http://localhost:8080/health | jq .
```

Записать turn:

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "smoke-1",
    "user_id": "user-1",
    "messages": [
      {
        "role": "user",
        "content": "I just moved to Berlin from NYC last month. Loving it so far."
      },
      {
        "role": "assistant",
        "content": "That sounds exciting! Berlin is a great city. How are you settling in?"
      }
    ],
    "timestamp": "2025-03-15T10:30:00Z",
    "metadata": {}
  }'
```

Сделать recall:

```bash
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Where does this user live?",
    "session_id": "smoke-2",
    "user_id": "user-1",
    "max_tokens": 512
  }'
```

Ожидается:

- response mentions Berlin
- ideally also mentions moved from NYC

Проверить memories:

```bash
curl http://localhost:8080/users/user-1/memories | jq .
```

Ожидается:

- structured memories
- not raw message chunks

---

# 27. How they will run the project

Они запустят примерно так:

```bash
git clone <your repo> memory-service
cd memory-service
docker compose up -d

until curl -sf http://localhost:8080/health; do sleep 1; done
```

Потом eval будет обращаться к:

```text
http://localhost:8080
```

Default port должен быть:

```text
8080
```

---

# 28. Automated private eval

Они прогонят закрытые сценарии.

Основные категории оценки:

---

## 28.1 Recall quality

Проверяют, достает ли `/recall` нужные факты.

Пример:

```text
User said earlier: I have a dog named Biscuit.
Query: What is the user's dog's name?
Expected: Biscuit
```

---

## 28.2 Fact evolution

Проверяют, умеет ли сервис обновлять факты.

Пример:

```text
I work at Stripe.
I just joined Notion.
```

Expected:

- current employer is Notion
- Stripe is historical/superseded
- recall returns Notion
- memories still show Stripe as inactive/superseded

---

## 28.3 Multi-hop recall

Проверяют, может ли сервис связать несколько memories.

Пример:

```text
User has a dog named Biscuit.
User lives in Berlin.
Query: What city does the user with the dog named Biscuit live in?
Expected: Berlin
```

---

## 28.4 Noise resistance

Проверяют, что сервис не галлюцинирует.

Пример:

```text
No memory about favorite movie.
Query: What is the user's favorite movie?
Expected: empty context
```

---

## 28.5 Extraction quality

Они смотрят endpoint:

```http
GET /users/{user_id}/memories
```

Проверяют:

- structured memories
- typed memories
- confidence
- provenance
- implicit facts
- corrections
- active/inactive state
- supersedes links

---

## 28.6 Persistence

Проверяют, что данные живут после:

```bash
docker compose down && docker compose up
```

---

## 28.7 Cross-session scoping

Проверяют, что разные sessions/users не смешиваются.

---

## 28.8 Robustness

Проверяют bad input.

Сервис не должен падать.

---

## 28.9 Contract compliance

Проверяют:

- endpoints
- status codes
- JSON shapes
- required fields
- response fields

---

# 29. Human architecture review

После automated eval они читают:

- code
- tests
- README
- CHANGELOG

Оценивают:

- насколько архитектура понятная
- насколько storage choice обоснован
- есть ли реальное extraction
- есть ли thoughtful recall strategy
- есть ли contradiction handling
- есть ли tests
- есть ли quality fixture
- насколько код поддерживаемый
- можно ли объяснить дизайн на интервью

Будет follow-up interview примерно на 30 минут.

Нужно будет защищать решения.

---

# 30. Что считается excellent

Excellent solution:

- полностью соблюдает HTTP contract
- запускается через Docker без ручной настройки
- использует persistent named volume
- stores raw turns separately from structured memories
- extracts typed structured memories
- supports fact/preference/opinion/event
- stores confidence
- stores provenance
- handles corrections
- handles contradictions
- old facts are superseded, not deleted
- history is visible
- `/recall` returns current active facts
- `/recall` does not hallucinate on unknown queries
- supports multi-hop-ish recall
- uses more than naive top-k raw chunk search
- has hybrid ranking or good rule-based ranking
- respects max_tokens approximately
- keeps `/turns` synchronous
- has clean `/users/{user_id}/memories`
- includes tests
- includes recall quality fixture
- includes 4+ changelog iterations
- README explains design clearly
- code is clean and maintainable

---

# 31. Что будет плохо

Плохие решения:

- просто сохранять все сообщения в vector DB
- возвращать raw chunks как memories
- не иметь `/users/{user_id}/memories`
- не обрабатывать fact evolution
- удалять старые факты полностью
- смешивать разных пользователей
- терять данные после restart
- делать extraction асинхронно после ответа
- падать на bad input
- не иметь tests
- не иметь changelog
- иметь README только с командами запуска без архитектуры
- использовать LLM без fallback и без env docs
- возвращать выдуманные факты, если ничего не найдено

---

# 32. Лучший быстрый стек

Для 2 дней лучше всего:

```text
Python + FastAPI + SQLite + FTS5 + deterministic structured extraction + optional LLM extraction
```

Почему:

- быстро писать
- просто Dockerize
- SQLite легко сохранять в volume
- FTS5 дает нормальный lexical search
- FastAPI быстро покрывает HTTP contract
- Pydantic дает validation
- можно сделать хороший deterministic fallback
- меньше DevOps, чем Postgres + pgvector

---

# 33. Более сильный стек

Если есть больше времени:

```text
Python + FastAPI + Postgres + pgvector + BM25 + reranking + structured memory graph
```

Плюсы:

- сильнее выглядит архитектурно
- ближе к production
- можно делать semantic retrieval
- можно делать hybrid retrieval

Минусы:

- больше setup
- больше вероятность Docker/db проблем
- больше времени на migrations
- сложнее успеть за 2 дня

---

# 34. Рекомендуемая архитектура для быстрого сильного решения

Оптимальный вариант:

```text
FastAPI service
    |
    |-- /turns
    |     |
    |     |-- validate request
    |     |-- save raw turn
    |     |-- extract structured memories
    |     |-- resolve conflicts
    |     |-- save active/inactive memories
    |     |-- update FTS index
    |     |
    |     '-- return 201 only after all done
    |
    |-- /recall
    |     |
    |     |-- load active user memories
    |     |-- load session memories
    |     |-- retrieve candidates by FTS/key/query intent
    |     |-- rank by relevance + confidence + recency + active status
    |     |-- assemble context under max_tokens
    |     '-- return context + citations
    |
    |-- /search
    |     |
    |     |-- search structured memories
    |     '-- return structured results
    |
    '-- /users/{user_id}/memories
          |
          '-- return all memories including superseded
```

---

# 35. Suggested database tables

Если делать SQLite, можно использовать такие таблицы.

## turns

```text
turns
- id
- session_id
- user_id
- timestamp
- messages_json
- metadata_json
- created_at
```

## memories

```text
memories
- id
- user_id
- session_id
- type
- key
- value
- normalized_key
- confidence
- source_turn
- source_session
- created_at
- updated_at
- supersedes
- active
- metadata_json
```

## memory_fts

```text
memory_fts
- memory_id
- content
```

Можно сделать FTS5 virtual table.

---

# 36. Suggested memory types

Использовать такие types:

```text
fact
preference
opinion
event
```

Можно добавить:

```text
correction
summary
relationship
```

Но для контракта достаточно первых четырех.

---

# 37. Suggested canonical keys

Для fact evolution удобно нормализовать key.

Примеры:

```text
current_city
previous_city
country
employer
job_title
pet
allergy
diet
relationship_status
answer_style
preferred_language
programming_language_preference
current_project
```

---

# 38. Suggested ranking formula

Можно сделать простую формулу:

```text
score =
  0.40 * lexical_match
+ 0.20 * key_intent_match
+ 0.15 * confidence
+ 0.15 * recency
+ 0.10 * active_bonus
```

Дополнительно:

- inactive memories не показывать, кроме случаев, когда query просит history
- user_id match важнее session match
- exact entity match получает boost
- canonical key match получает boost

---

# 39. Suggested recall context format

Хороший формат:

```md
## Known facts about this user
- Currently lives in Berlin (updated 2025-03-15; previously NYC)
- Works at Notion (updated 2025-03-15; previously Stripe)
- Has a dog named Biscuit

## Preferences and opinions
- Prefers concise, direct answers
- Likes Python for scripts and TypeScript for larger projects

## Relevant recent context
- [2025-03-15] User moved to Berlin from NYC and is settling in
```

---

# 40. Main takeaway

Это задание проверяет не CRUD и не обычный vector search.

Они хотят увидеть, что ты понимаешь memory systems для AI agents.

Ключевые слова, которые должны быть в реализации и README:

- structured memories
- extraction pipeline
- provenance
- confidence
- canonical keys
- fact evolution
- superseded memories
- active memories
- contradiction handling
- correction handling
- user-level memory
- session-level memory
- hybrid recall
- token budget
- recall quality fixture
- Docker persistence
- synchronous correctness
- robust input validation
- changelog iterations

---

# 41. Короткий чеклист перед сдачей

Перед отправкой проверь:

- [ ] Репозиторий называется `higgsfield-memory-service`
- [ ] `docker compose up` запускает сервис
- [ ] сервис слушает port `8080`
- [ ] `GET /health` возвращает `200`
- [ ] `POST /turns` возвращает `201` и `{ "id": "..." }`
- [ ] после `/turns` сразу работает `/recall`
- [ ] `/recall` возвращает `context` и `citations`
- [ ] empty recall возвращает `context: ""` и `citations: []`
- [ ] `/search` возвращает `results`
- [ ] `/users/{user_id}/memories` возвращает structured memories
- [ ] memories не являются raw chunks
- [ ] есть `type`, `key`, `value`, `confidence`, `source_turn`, `active`
- [ ] fact evolution работает
- [ ] old facts marked inactive/superseded
- [ ] delete session работает
- [ ] delete user работает
- [ ] данные сохраняются после restart
- [ ] разные users/sessions не смешиваются
- [ ] malformed input дает 4xx
- [ ] есть tests
- [ ] есть recall quality fixture
- [ ] есть README с architecture/storage/extraction/recall/fact evolution/tradeoffs/failure modes/tests
- [ ] есть CHANGELOG с 4+ итерациями
- [ ] `.env.example` содержит все env vars
- [ ] smoke test из задания проходит