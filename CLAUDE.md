# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An AI copilot for customer support agents. It exposes a **FastAPI backend** (`main.py`) and a **Streamlit dashboard** (`app.py`). The agent generates draft replies by combining:
1. Customer memory (Mem0 + ChromaDB)
2. RAG knowledge base (ChromaDB)
3. LangChain agent with Groq LLM + tools (live DB lookups)

## Commands

**Package manager:** `uv` (Python 3.11 required)

```bash
# Install dependencies
uv sync

# Run the API server (http://localhost:8000)
uv run python main.py

# Run with hot-reload during development
uv run uvicorn main:app --reload --port 8000

# Run the Streamlit dashboard (http://localhost:8501)
uv run streamlit run app.py

# Run tests
uv run pytest

# Run a single test
uv run pytest tests/test_simple.py::test_health_endpoint_returns_ok -v

# Docker (API + dashboard together)
docker compose up --build
```

## Environment Variables

Create a `.env` file at the project root. Required:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | LLM inference (required for draft generation) |
| `GOOGLE_API_KEY` | Gemini embeddings for Mem0 + ChromaDB RAG (recommended) |
| `OPENAI_API_KEY` | Alternative embedding provider |
| `ENABLE_LOCAL_EMBEDDINGS` | Set `true` to use HuggingFace `all-MiniLM-L6-v2` (requires `sentence-transformers`) |

At least one embedding provider must be configured for Mem0 to function.

## Architecture

```
main.py              → creates FastAPI app via create_app()
app.py               → Streamlit dashboard; calls FastAPI over HTTP
customer_support_agent/
  api/               → FastAPI routers (tickets, drafts, knowledge, memory, health)
  core/settings.py   → Pydantic-settings config; all paths resolved from workspace_dir
  services/
    copilot_service.py   → SupportCopilot: the central AI orchestrator
    draft_service.py     → Creates/updates drafts; calls SupportCopilot
    knowledge_service.py → Wraps KnowledgeBaseService for ingestion endpoint
  integrations/
    memory/mem0_store.py → Mem0-backed memory store (ChromaDB vector store)
    rag/chroma_kb.py     → ChromaDB RAG: ingest .md/.txt files, semantic search
    tools/support_tools.py → LangChain tools: lookup_customer_plan, lookup_open_ticket_load
  repositories/sqlite/ → SQLite access for customers, tickets, drafts
  schemas/api.py       → Pydantic request/response models
knowledge_base/        → .md files ingested into ChromaDB RAG
data/                  → Runtime data (support.db, chroma_rag/, chroma_mem0/)
```

### Request flow for draft generation

1. `POST /api/tickets/{id}/generate-draft` → `DraftService` → `SupportCopilot.generate_draft()`
2. `SupportCopilot` fetches memory hits (Mem0, scoped by email + company) and KB hits (ChromaDB)
3. Builds system + user prompts with retrieved context, runs LangChain agent (Groq LLM)
4. Extracts draft text from agent messages; falls back to direct LLM call if agent returns empty content
5. Saves `context_used` dict (version 2 schema with signals, highlights, tool_calls) alongside the draft

### Memory scoping

Memory is stored under two scopes per interaction:
- **Customer scope:** `customer_email.strip().lower()`
- **Company scope:** `company::{normalized_company_name}` (if company is set)

When accepting a draft, `save_accepted_resolution()` writes to both scopes so future tickets for the same company benefit from prior resolutions.

### Knowledge base ingestion

Add `.md` or `.txt` files to `knowledge_base/`. Trigger ingestion via the Streamlit sidebar ("Ingest Knowledge Base") or `POST /api/knowledge/ingest`. The ChromaDB collection name is `support_kb_gemini` when `GOOGLE_API_KEY` is set, `support_kb` otherwise — switching embedding providers requires clearing the old collection.

### LangChain tools (support_tools.py)

- `lookup_customer_plan`: deterministic hash on email → returns plan tier + SLA hours (no real billing system)
- `lookup_open_ticket_load`: queries SQLite for real open ticket count

To add a new tool, implement it with `@tool` decorator and register it in `get_support_tools()`.
