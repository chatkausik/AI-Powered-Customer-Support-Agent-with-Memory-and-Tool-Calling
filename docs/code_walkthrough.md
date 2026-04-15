# Code Walkthrough

A module-by-module explanation of every file in the project.

---

## Entry points

### `main.py`

```python
app = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.api_host, port=settings.api_port, reload=False)
```

The FastAPI `app` object is created by `create_app()` (from `api/app_factory.py`) and assigned at module level. This lets uvicorn import `main:app` directly. When the file is run as a script, uvicorn is started programmatically using values from `Settings`.

---

### `app.py` — Streamlit dashboard

The dashboard is a single-file Streamlit app. It communicates with the FastAPI backend **exclusively over HTTP** — it has no direct imports from `customer_support_agent`. This keeps the two processes fully decoupled and mirrors how a real frontend would interact with the API.

Key sections:

| Section | What it does |
|---|---|
| `fetch_tickets()` | `GET /api/tickets` with a 10-second `st.cache_data` TTL |
| `create_ticket()` | `POST /api/tickets`, clears cache on success |
| `trigger_draft()` | `POST /api/tickets/{id}/generate-draft` (60s timeout) |
| `update_draft()` | `PATCH /api/drafts/{id}` with content + status |
| `ingest_knowledge()` | `POST /api/knowledge/ingest` |
| `search_memory()` | `GET /api/customers/{id}/memory-search` |
| `render_context()` | Renders the `context_used` v2 payload — signals, highlights, tool call table |

`_extract_api_error()` normalises FastAPI validation errors (which return a list of dicts) into a human-readable string for `st.error()`.

---

## `customer_support_agent/core/`

### `settings.py`

Uses **pydantic-settings** to load config from environment variables and a `.env` file. All settings have sensible defaults so the app starts without any `.env` (though draft generation requires API keys).

Important fields:

| Field | Default | Purpose |
|---|---|---|
| `groq_api_key` | `""` | Groq LLM API key — required for draft generation |
| `groq_model` | `llama-3.1-8b-instant` | Model used for inference |
| `google_api_key` | `""` | Gemini embedding provider (recommended) |
| `openai_api_key` | `""` | Alternative embedding provider |
| `enable_local_embeddings` | `False` | Uses HuggingFace `all-MiniLM-L6-v2` locally |
| `chroma_rag_dir` | `data/chroma_rag` | RAG vector store path |
| `chroma_mem0_dir` | `data/chroma_mem0` | Memory vector store path |
| `rag_chunk_size` | `800` | Characters per knowledge base chunk |
| `rag_chunk_overlap` | `120` | Overlap between consecutive chunks |
| `rag_top_k` | `4` | Max RAG results per query |
| `mem0_top_k` | `5` | Max memory results per query |

`resolve(path)` converts relative `Path` objects to absolute paths anchored at `workspace_dir` (the project root). This ensures paths work correctly regardless of the working directory when the server is started.

`effective_google_embedding_model` normalises deprecated Gemini model IDs (e.g. `text-embedding-004`) to the current recommended model `gemini-embedding-001` automatically.

`get_settings()` is decorated with `@lru_cache` so the same `Settings` object is returned across all calls — environment variables are read only once at startup.

`ensure_directories()` creates `data/`, `data/chroma_rag/`, `data/chroma_mem0/`, and `knowledge_base/` if they don't exist. Called during FastAPI startup lifespan.

---

## `customer_support_agent/api/`

### `app_factory.py`

```python
def create_app(settings: Settings | None = None) -> FastAPI:
```

Accepts an optional `settings` override — this is used by tests, which pass a `tmp_path`-based settings to isolate the database. The `lifespan` async context manager runs `ensure_directories()` and `init_db()` before the server starts accepting requests. All five routers are registered under their respective `/api/...` prefixes.

### `dependencies.py`

FastAPI dependency functions injected into route handlers via `Depends()`.

`get_copilot()` is decorated with `@lru_cache` — **`SupportCopilot` is a singleton** for the lifetime of the process. This matters because:
- `SupportCopilot` holds the Groq LLM client, Mem0 store, and ChromaDB client.
- Initialising these on every request would be expensive.
- The LangChain agent's `InMemorySaver` checkpointer is shared across requests (per-thread-id state).

`get_copilot_or_503()` wraps `get_copilot()` and converts any initialization exception into a `503 Service Unavailable` HTTP error, giving the caller a useful error instead of a 500.

### Routers

#### `routers/tickets.py`

| Route | Method | Behaviour |
|---|---|---|
| `/api/tickets` | POST | Creates customer (upsert by email), creates ticket, optionally enqueues background draft generation |
| `/api/tickets` | GET | Lists all tickets (joined with customer data), newest first |
| `/api/tickets/{id}` | GET | Fetches single ticket |
| `/api/tickets/{id}/generate-draft` | POST | Synchronously generates and stores a draft (used by "Generate Draft" button) |

**Auto-generate flow:** When `auto_generate=True` (the default), draft generation is added as a `BackgroundTask`. The ticket creation response is returned immediately; the draft appears a few seconds later when the client next polls.

**Manual generate flow:** The `/generate-draft` route calls `generate_and_store_manual()` synchronously. It blocks until the LLM responds (typically 5–15s with Groq).

#### `routers/drafts.py`

| Route | Method | Behaviour |
|---|---|---|
| `/api/drafts/{ticket_id}` | GET | Returns the latest draft for a ticket |
| `/api/drafts/{draft_id}` | PATCH | Updates draft content and/or status |

**Accept flow (PATCH with `status=accepted`):**
1. Draft row is updated in SQLite.
2. Ticket status is set to `"resolved"`.
3. `save_accepted_resolution()` is scheduled as a `BackgroundTask` — it calls Mem0 to store the resolution. This is done in the background to avoid the ~20–30s Mem0 LLM extraction blocking the HTTP response.

#### `routers/knowledge.py`

Single `POST /api/knowledge/ingest` endpoint. Delegates entirely to `KnowledgeService.ingest()`. Accepts `clear_existing: bool` to wipe and rebuild the ChromaDB collection.

#### `routers/memory.py`

| Route | Behaviour |
|---|---|
| `GET /api/customers/{id}/memories` | Lists all stored memories for a customer (both email and company scopes) |
| `GET /api/customers/{id}/memory-search` | Semantic search over customer memories with a query string |

Both routes look up the customer by ID first, then pass `email` and `company` to `SupportCopilot` which handles scope resolution.

Both responses include `memory_available: bool` and `memory_note: str | None`. When the Mem0 backend is unavailable, `memory_available` is `false` and `memory_note` explains which environment variable to set — the endpoint still returns 200 with an empty result rather than failing.

#### `routers/health.py`

Returns `{"status": "ok"}` — used by Docker healthcheck and CI/CD pipeline.

---

## `customer_support_agent/services/`

### `copilot_service.py` — `SupportCopilot`

This is the heart of the system. Responsible for all AI operations.

**`__init__`**

Initialises three components:
1. `ChatGroq` — LangChain LLM wrapper for Groq API.
2. `CustomerMemoryStore` — Mem0 memory backend. If initialisation fails (e.g. no embedding provider configured), `self.memory` is set to `None` and `self._memory_error` captures the reason. Draft generation continues without memory — the error is surfaced in `context_used["errors"]` as a `memory_skipped:` entry.
3. `KnowledgeBaseService` — ChromaDB RAG client.

**`memory_available` property** — Returns `True` only when `self.memory is not None`. Used by memory API endpoints to set the `memory_available` field in responses.

The LangChain agent is built with `create_agent()` using `InMemorySaver` as the checkpointer. Each ticket gets its own thread ID (`ticket::{ticket_id}`) so multi-turn agent state is scoped per ticket.

**`generate_draft(ticket, customer)`**

The main pipeline:

```
1. _search_memory_scopes()   → memory hits (Mem0)
2. rag.search()              → knowledge hits (ChromaDB)
3. _build_system_prompt()    → embeds memory + KB context into system message
4. _build_user_prompt()      → formats ticket + customer details
5. agent.invoke()            → LangChain agent runs (may call tools)
6. _extract_agent_draft_and_tool_calls() → parses agent messages
7. Fallback chain:
   a. _fallback_generate_text()    → direct LLM call with no tools
   b. _deterministic_fallback()    → static template (never fails)
8. _build_context()          → assembles context_used v2 payload
```

**Memory scoping**

Each customer has two memory scopes:
- `customer_email.strip().lower()` — individual customer scope
- `company::{normalised_company_name}` — company-wide scope

When searching, both scopes are queried and results are merged and deduplicated. When saving accepted resolutions, both scopes are written to. This means if two agents handle different tickets for the same company, they both benefit from previously accepted resolutions.

**`_extract_agent_draft_and_tool_calls(agent_result)`**

Walks the agent's message list in reverse to find the last non-empty `AIMessage` (the draft). Also collects all `AIMessage.tool_calls` and their corresponding `ToolMessage` results, building a structured trace for each tool invocation (name, arguments, status, output, summary).

**`_extract_entity_links()`**

Extracts semantic tags from the ticket+draft text to enrich memory entries:
- API endpoints (`/some/path`)
- HTTP status codes (`4xx`, `5xx`)
- Geographic regions (EU, US, APAC, India)
- Integrations (Shopify, Stripe, Salesforce, etc.)
- Plan tier and billing risk from tool outputs

These tags are stored as metadata alongside the Mem0 memory entries.

---

### `draft_service.py` — `DraftService`

Handles the lifecycle of draft records and serialisation.

**`generate_and_store_background()`** — Called when a ticket is created with `auto_generate=True`. Accepts a `copilot_factory` callable (rather than the copilot directly) so it can call `get_copilot()` inside the background thread — important because FastAPI's DI container is not available inside `BackgroundTask`.

**`generate_and_store_manual()`** — Called by the manual `/generate-draft` endpoint. Takes the copilot directly (already resolved by DI).

**`_normalize_draft_result()`** — Ensures the draft text is never empty by substituting a generic fallback message if the copilot returned nothing.

**`serialize_draft()`** — Deserialises the `context_used` JSON string from SQLite into a Python dict before returning.

---

### `knowledge_service.py` — `KnowledgeService`

Thin wrapper around `KnowledgeBaseService.ingest_directory()`. Its only purpose is to keep the router layer free of direct integration dependencies.

---

## `customer_support_agent/integrations/`

### `memory/mem0_store.py` — `CustomerMemoryStore`

Wraps the [Mem0](https://github.com/mem0ai/mem0) library with a clean interface.

**Initialisation** builds the Mem0 config dict dynamically based on which API key is available:
- `GOOGLE_API_KEY` → Gemini embeddings (recommended, lowest cost)
- `OPENAI_API_KEY` → OpenAI `text-embedding-3-small`
- `ENABLE_LOCAL_EMBEDDINGS=true` → HuggingFace `all-MiniLM-L6-v2`

The vector store is always `chroma` with a persistent path (`data/chroma_mem0/`).

**`add_resolution()`** stores a ticket resolution as a two-turn conversation:
```
user: "Ticket subject: {subject}\nProblem: {description}"
assistant: "Resolution accepted by support agent:\n{draft}{entity_links}"
```
Mem0 processes this with its own LLM call to extract and store discrete memory facts.

**`_normalize_results()`** handles both old Mem0 return formats (`list` and `{"results": [...]}`) for compatibility across library versions.

---

### `rag/chroma_kb.py` — `KnowledgeBaseService`

Manages the knowledge base vector store.

**Embedding function selection:** If `GOOGLE_API_KEY` is set, uses `GoogleGenaiEmbeddingFunction` (Gemini). Otherwise falls back to ChromaDB's default (`all-MiniLM-L6-v2` local model). The ChromaDB collection name changes between providers (`support_kb_gemini` vs `support_kb`) to avoid mixing embeddings from different models.

**`ingest_directory()`** scans `knowledge_base/` for `.md` and `.txt` files, splits them with `RecursiveCharacterTextSplitter`, and upserts chunks into ChromaDB. Each chunk gets a stable ID (`{stem}-{index}-{sha1_hash}`) so re-ingesting the same file is idempotent.

**`search()`** returns the top-K chunks with `content`, `source` (filename), and `distance` fields.

---

### `integrations/tools/support_tools.py`

LangChain tools decorated with `@tool`. The docstring of each tool is the description the LLM sees when deciding whether to call it.

**`lookup_customer_plan(customer_email)`** — Returns a deterministic plan tier (free/starter/pro/enterprise) based on a SHA-256 hash of the email. This simulates a billing system without requiring one. The output includes SLA hours and whether priority queue handling applies.

**`lookup_open_ticket_load(customer_email)`** — Queries SQLite for the real count of open tickets for this customer. Returns a load band (light/moderate/heavy) and a recommended action.

Both tools return structured JSON strings (not Python dicts) because LangChain tool outputs must be strings. The `_json()` helper handles serialisation.

`get_support_tools()` returns all registered tools. Currently: `[lookup_customer_plan, lookup_open_ticket_load, analyze_ticket_sentiment]`.

---

### `integrations/tools/sentiment_tools.py`

Implements `analyze_ticket_sentiment(subject, description)` — a `@tool` that the agent calls to understand the emotional tone of a ticket before composing a reply.

**Analysis approach:** Pure keyword/rule matching across three tiers:

| Tier | Examples | Result |
|---|---|---|
| High-escalation patterns | `charged \d+ times`, `nobody.*help`, `refund`, `lawsuit`, `cancel account` | `escalation_risk: "high"` |
| Negative patterns | `not working`, `error`, `broken`, `frustrated`, `urgent` | `escalation_risk: "medium"/"low-medium"` |
| Positive/neutral patterns | `thank`, `checking`, `just wondering`, `limits` | `escalation_risk: "low"` |

Returns a JSON string with `sentiment`, `confidence`, `escalation_risk`, `summary`, and `recommended_action`.

**Caching:** The inner `_analyze(subject, description)` function is wrapped with `@functools.lru_cache(maxsize=512)`. Repeated calls with identical inputs return cached results without re-running the regex engine.

---

## `customer_support_agent/repositories/sqlite/`

### `base.py`

`connect()` opens a new SQLite connection for each call. `check_same_thread=False` is needed because uvicorn runs background tasks on worker threads. `PRAGMA foreign_keys = ON` enforces referential integrity.

`init_db()` creates the three tables (`customers`, `tickets`, `drafts`) and a trigger that updates `tickets.updated_at` on any row change. All `CREATE TABLE IF NOT EXISTS` so it's safe to call on every startup.

### `customers.py` — `CustomersRepository`

`create_or_get()` implements an upsert pattern: if the customer exists, it backfills missing `name`/`company` fields (without overwriting existing values). This means a customer record is enriched over time as new tickets arrive with more detail.

`get_by_email()` looks up a customer by email address (`WHERE email = ?`). Note: an earlier version of this method had a bug where it queried `WHERE id = ?` with an email string, causing it to always return `None`. This was caught by `test_get_by_email_returns_correct_customer` in the test suite.

### `tickets.py` — `TicketsRepository`

All read queries (`list`, `get_by_id`) JOIN with the `customers` table to include `customer_email`, `customer_name`, and `customer_company` in results — avoiding the need for a separate customer lookup in most cases.

`count_open_for_customer()` is called by the `lookup_open_ticket_load` tool to get a real ticket count.

### `drafts.py` — `DraftsRepository`

`get_latest_for_ticket()` always returns the most recent draft (ORDER BY created_at DESC LIMIT 1). Multiple drafts can exist for a ticket (e.g. after retrying generation).

`get_ticket_and_customer_by_draft()` performs a three-way JOIN (drafts → tickets → customers) in a single query. Used by the accept flow to get all the data needed to save a resolution to Mem0 without extra lookups.

---

## `customer_support_agent/schemas/api.py`

Pydantic v2 models for all API request and response bodies.

`DraftResponse` embeds `StructuredDraftContext` (the `context_used` field). This model accepts both a fully typed `StructuredDraftContext` object and a raw `dict` to gracefully handle legacy or malformed context blobs stored in SQLite.

`TicketCreateRequest` uses `EmailStr` for validation and `Field(min_length=...)` constraints on `subject` and `description` — these are the fields validated before a ticket is even created.

---

## `tests/`

The test suite has 34 tests across 7 files, all isolated from real data and external APIs.

### `conftest.py`

Shared pytest fixtures used across the entire suite:

- **`test_settings(tmp_path)`** — Creates a `Settings` object pointing at a temp directory with all API keys blank. Each test gets its own `tmp_path` (pytest built-in), so tests never share state.
- **`patched_db(test_settings)`** — Patches `base.get_settings` at the module level so all `connect()` calls inside repositories use the temp SQLite file. Calls `init_db()` to create the schema before yielding.
- **`api_client(test_settings)`** — Applies the same `base.get_settings` patch, then creates the FastAPI app and a `TestClient`. The app's lifespan (which calls `init_db()`) runs inside the patch, so the API and repositories all share the same isolated DB.
- **`_clear_copilot_cache()` (autouse)** — Calls `get_copilot.cache_clear()` before and after every test. Without this, `@lru_cache` on `get_copilot()` would let one test's copilot instance bleed into another.

### `test_health.py`

Basic smoke tests for `GET /health`.

### `test_tickets_api.py`

Covers create, list, get-by-id, 404 for unknown id, and customer deduplication across two tickets from the same email. All ticket tests use `auto_generate=False` to avoid triggering LLM calls.

### `test_drafts_api.py`

Seeds data directly via repositories (bypassing the API) to pre-create customers, tickets, and drafts, then exercises `GET /api/drafts/{ticket_id}` and `PATCH /api/drafts/{draft_id}` for content updates and status transitions.

### `test_knowledge_service.py`

Tests `KnowledgeBaseService` in isolation. Uses `_FakeEmbeddingFunction` — a deterministic hash-based embedding function that satisfies ChromaDB's interface (`name()`, `embed_query()`, `embed_documents()`) without downloading any model. Covers empty-collection search, ingestion, file-type filtering, and `clear_existing` rebuild.

### `test_customers_repository.py`

Seven tests covering the full `CustomersRepository` interface. Includes a regression test (`test_get_by_email_returns_correct_customer`) that would catch the `WHERE id = ?` vs `WHERE email = ?` bug.

### `test_support_tools.py`

Nine tests covering all three LangChain tools. No LLM is called — `lookup_customer_plan` and `analyze_ticket_sentiment` are deterministic, and `lookup_open_ticket_load` uses the patched SQLite DB. Verifies JSON structure, determinism, load band calculation, and sentiment tier correctness.
