# AI-Powered Customer Support Agent Copilot

An intelligent copilot that helps customer support agents write better replies — faster. It combines a **LangChain agent** (Groq LLM), **persistent customer memory** (Mem0 + ChromaDB), and a **RAG knowledge base** (ChromaDB) to auto-draft context-aware responses for every support ticket.

---

## What it does

- **Auto-generates draft replies** for support tickets by pulling together customer history, relevant knowledge-base articles, and live account data from tools.
- **Remembers resolutions** — when an agent accepts a draft, the interaction is stored in Mem0 so future tickets for the same customer or company benefit from past context.
- **Searches a knowledge base** of markdown/text files using semantic search (Google Gemini embeddings by default).
- **Calls live tools** during draft generation to check subscription plan, SLA tier, open ticket load, and customer sentiment.
- **Streamlit dashboard** lets agents create tickets, trigger/edit drafts, probe customer memory, and accept or discard responses.

---

## Architecture overview

```
Streamlit Dashboard (app.py)
        │  HTTP
        ▼
FastAPI Backend (main.py)
        │
        ├── /api/tickets          → create, list, get tickets
        ├── /api/tickets/{id}/generate-draft
        ├── /api/drafts/{id}      → get, update (accept/discard)
        ├── /api/knowledge/ingest → index knowledge_base/ into ChromaDB
        └── /api/customers/{id}/memory-search
                │
                ▼
        SupportCopilot (services/copilot_service.py)
                │
                ├── Mem0 + ChromaDB  → customer memory (per email + per company)
                ├── ChromaDB RAG     → knowledge base semantic search
                └── LangChain Agent (Groq LLM)
                        └── Tools: lookup_customer_plan, lookup_open_ticket_load, analyze_ticket_sentiment
```

**Data stores**

| Store | Purpose | Location |
|---|---|---|
| SQLite | Customers, tickets, drafts | `data/support.db` |
| ChromaDB (RAG) | Knowledge base chunks | `data/chroma_rag/` |
| ChromaDB (Mem0) | Customer memory vectors | `data/chroma_mem0/` |

---

## Quick start

### 1. Prerequisites

- Python 3.11
- [`uv`](https://github.com/astral-sh/uv) package manager

```bash
pip install uv
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure environment

Create a `.env` file in the project root:

```env
# Required — LLM inference
GROQ_API_KEY=your_groq_api_key

# Required — one embedding provider must be set
GOOGLE_API_KEY=your_google_api_key       # recommended (Gemini embeddings)
# OPENAI_API_KEY=your_openai_api_key     # alternative

# Optional tuning
GROQ_MODEL=llama-3.1-8b-instant
LLM_TEMPERATURE=0.2
RAG_TOP_K=4
MEM0_TOP_K=5
```

> Get a free Groq API key at [console.groq.com](https://console.groq.com).  
> Get a Google API key at [aistudio.google.com](https://aistudio.google.com).

### 4. Run locally

Start the API server:
```bash
uv run python main.py
```

Start the Streamlit dashboard (separate terminal):
```bash
uv run streamlit run app.py
```

- API: http://localhost:8000
- API docs (Swagger): http://localhost:8000/docs
- Dashboard: http://localhost:8501

### 5. Ingest the knowledge base

Click **"Ingest Knowledge Base"** in the Streamlit sidebar, or:

```bash
curl -X POST http://localhost:8000/api/knowledge/ingest \
  -H "Content-Type: application/json" \
  -d '{"clear_existing": false}'
```

---

## Running with Docker

Both services together:

```bash
docker compose up --build
```

- API: http://localhost:8000
- Dashboard: http://localhost:8501

The dashboard container automatically points to the API container via Docker's internal network.

---

## Running tests

```bash
uv run pytest              # all tests (34 tests across 6 files)
uv run pytest -v           # verbose output
uv run pytest --cov        # with coverage report (≥50% enforced)
```

---

## Project structure

```
├── main.py                   # FastAPI app entry point
├── app.py                    # Streamlit dashboard
├── pyproject.toml            # Dependencies (managed by uv)
├── Dockerfile
├── docker-compose.yml
├── knowledge_base/           # Add .md or .txt files here for RAG
│   ├── banking-atm-cash-withdrawal-faq.md
│   ├── banking-charges-and-minimum-balance.md
│   ├── banking-kyc-and-account-update-rules.md
│   └── saving-account-rule.md
├── data/                     # Runtime data (auto-created)
│   ├── support.db            # SQLite database
│   ├── chroma_rag/           # RAG vector store
│   └── chroma_mem0/          # Memory vector store
├── customer_support_agent/
│   ├── api/                  # FastAPI routers + app factory
│   ├── core/                 # Settings (pydantic-settings)
│   ├── integrations/
│   │   ├── memory/           # Mem0 memory store
│   │   ├── rag/              # ChromaDB knowledge base
│   │   └── tools/            # LangChain tools (plan, ticket load, sentiment)
│   ├── repositories/         # SQLite data access
│   ├── schemas/              # Pydantic request/response models
│   └── services/             # Business logic (copilot, drafts, knowledge)
├── docs/
│   ├── code_walkthrough.md   # Detailed per-module code explanation
│   ├── knowledge_base_guide.md  # How to extend and manage the knowledge base
│   └── EC2_deployment_flow.md   # AWS EC2 + GitHub Actions CI/CD guide
└── tests/
    ├── conftest.py               # shared fixtures (isolated DB, TestClient)
    ├── test_health.py
    ├── test_tickets_api.py
    ├── test_drafts_api.py
    ├── test_knowledge_service.py
    ├── test_customers_repository.py
    ├── test_support_tools.py
    └── test_simple.py
```

---

## How draft generation works

1. Agent receives ticket subject + description + customer profile.
2. **Memory search** — Mem0 is queried for prior resolutions for this customer email and their company.
3. **RAG search** — ChromaDB returns the top-K relevant knowledge-base chunks.
4. **LangChain agent** builds a system prompt embedding the memory + KB context, then runs with three tools available: `lookup_customer_plan`, `lookup_open_ticket_load`, `analyze_ticket_sentiment`.
5. Agent response is extracted. If empty, a direct LLM fallback call is made; if still empty, a deterministic template is used.
6. The full `context_used` payload (signals, memory hits, KB hits, tool calls, errors) is stored alongside the draft for transparency. If memory is unavailable, `errors` will contain a `memory_skipped:` entry and draft generation continues normally.
7. When the agent **accepts** a draft, the resolution is saved back to Mem0 asynchronously — enriching future drafts for the same customer/company.

---

## Adding to the knowledge base

Drop `.md` or `.txt` files into `knowledge_base/` and re-ingest. Files are split into overlapping chunks (default 800 chars / 120 overlap) and stored in ChromaDB with the filename as source metadata.

> **Note:** If you switch embedding providers (e.g. from default to Gemini), ingest with `clear_existing: true` to rebuild the collection with the new embeddings.

---

## Deployment to AWS EC2

See [`docs/EC2_deployment_flow.md`](docs/EC2_deployment_flow.md) for a complete GitHub Actions CI/CD pipeline guide.
