# Knowledge Base Guide

Everything you need to know about how the knowledge base works, how to extend it, and how to troubleshoot it.

---

## What the knowledge base is

The knowledge base is a folder of plain-text files (`knowledge_base/`) that are indexed into a ChromaDB vector store. When the AI copilot generates a draft reply, it semantically searches this store and injects the most relevant snippets directly into the LLM's system prompt — giving the agent factual, policy-grounded answers without hallucination.

This is a standard **Retrieval-Augmented Generation (RAG)** pattern:

```
User ticket
    │
    ▼
Embed query  ──►  Search ChromaDB  ──►  Top-K chunks
                                              │
                                              ▼
                              Injected into LLM system prompt
                                              │
                                              ▼
                                    Draft reply (grounded in KB)
```

---

## Current knowledge base files

| File | Topic |
|---|---|
| `banking-atm-cash-withdrawal-faq.md` | Daily limits, cash-not-dispensed reversals, card blocking |
| `banking-charges-and-minimum-balance.md` | Account maintenance fees, minimum balance rules |
| `banking-kyc-and-account-update-rules.md` | KYC document requirements, account update procedures |
| `saving-account-rule.md` | Interest rates, eligibility, account rules |

---

## How ingestion works

When you click **"Ingest Knowledge Base"** in the Streamlit sidebar (or call `POST /api/knowledge/ingest`), the following happens:

1. All `.md` and `.txt` files in `knowledge_base/` are read.
2. Each file is split into overlapping chunks using `RecursiveCharacterTextSplitter`:
   - **Chunk size:** 800 characters
   - **Overlap:** 120 characters (so context at chunk boundaries is not lost)
3. Each chunk is assigned a stable ID: `{filename_stem}-{chunk_index}-{sha1_of_content[:10]}`
4. Chunks are **upserted** into ChromaDB (re-ingesting the same file is safe — it updates, not duplicates).
5. The response tells you how many files and chunks were indexed.

### Chunk size guidance

| Document type | Recommended chunk size |
|---|---|
| Short FAQ entries | 400–600 chars |
| Policy paragraphs | 600–900 chars (default) |
| Long technical docs | 1000–1200 chars |

Adjust `RAG_CHUNK_SIZE` and `RAG_CHUNK_OVERLAP` in `.env` if needed.

---

## Adding new documents

1. Create a `.md` or `.txt` file in `knowledge_base/`.
2. Re-ingest via the Streamlit sidebar or:

```bash
curl -X POST http://localhost:8000/api/knowledge/ingest \
  -H "Content-Type: application/json" \
  -d '{"clear_existing": false}'
```

That's it. No code changes needed.

### Writing effective KB documents

The quality of RAG retrieval depends heavily on how documents are written. Follow these guidelines:

**Be specific, not general.** The LLM already has general world knowledge. Your KB should contain specific policies, limits, procedures, and facts that are unique to your organisation.

```markdown
# Good
Standard savings accounts have a minimum balance of INR 1,000.
Below this, a quarterly fee of INR 150 applies.

# Bad
Maintaining a minimum balance is important for customers.
```

**Use consistent terminology.** If your customers say "ATM withdrawal limit" and your policy says "daily cash dispensation cap", pick one and use it everywhere. The embedding model will find semantic matches, but identical terms score higher.

**Keep sections short and focused.** One concept per section is ideal. The chunk splitter does not understand your document structure — it splits on character count. If a section is 2000 characters long, it will be split mid-sentence. Write shorter, self-contained sections.

**Include the question that the chunk answers.** FAQ format works well because the question itself provides additional semantic signal:

```markdown
## What happens if cash is debited but not dispensed?
Reversal is processed within 24 hours automatically.
If not reversed within 24 hours, raise a dispute with:
- Transaction date and time
- ATM location
- Last 4 digits of your card
```

**Name files descriptively.** The filename is stored as the `source` metadata field and shown in the Streamlit "Context used" panel. `banking-atm-faq.md` is more useful to an agent than `doc1.md`.

---

## Switching embedding providers

The collection name in ChromaDB changes based on the active provider:
- `GOOGLE_API_KEY` set → collection `support_kb_gemini`
- No Google key → collection `support_kb` (local model)

**If you switch providers, you must rebuild the collection** — you cannot mix embeddings from different models in one collection:

```bash
curl -X POST http://localhost:8000/api/knowledge/ingest \
  -H "Content-Type: application/json" \
  -d '{"clear_existing": true}'
```

This deletes the old collection and re-indexes everything from scratch.

---

## How retrieval works at query time

When a draft is generated, the ticket subject + description is used as the search query:

```python
query = f"{ticket['subject']}\n{ticket['description']}"
kb_hits = rag.search(query=query, top_k=settings.rag_top_k)
```

ChromaDB performs approximate nearest-neighbour search using L2 distance on the embedded query vector. The top-K results (default: 4) are returned with:

- `content` — the raw chunk text
- `source` — the source filename
- `distance` — lower is more similar (not normalised to 0–1)

These chunks are formatted and injected into the LLM system prompt:

```
Knowledge Base Context:
- [banking-atm-cash-withdrawal-faq.md] Reversal is typically processed within 24 hours...
- [banking-charges-and-minimum-balance.md] Below the minimum balance, a quarterly fee of INR 150...
```

The LLM is instructed to reference KB facts when relevant, without exposing internal chain-of-thought.

---

## Tuning retrieval quality

### Increase `rag_top_k`

Returns more chunks per query — useful if relevant information is spread across multiple sections. Default is 4. Set `RAG_TOP_K=6` in `.env` for broader retrieval.

### Check what's being retrieved

Use the Streamlit dashboard → select a ticket → click "Generate Draft" → expand "Context used" → check "Detailed Knowledge Hits". You can see exactly which chunks were retrieved and their distance scores.

### Inspect the collection directly

```python
import chromadb
client = chromadb.PersistentClient(path="data/chroma_rag")
collection = client.get_collection("support_kb_gemini")  # or "support_kb"
print(collection.count())  # total chunks
results = collection.query(query_texts=["ATM withdrawal limit"], n_results=3)
```

### Re-index after editing documents

The upsert mechanism uses content hashes for chunk IDs. If you edit a file, the new content has a different hash, so new chunks are inserted alongside old ones. To ensure old chunks are removed, use `clear_existing: true` when re-ingesting.

---

## Knowledge base vs. customer memory

These are two different retrieval systems serving different purposes:

| | Knowledge Base (RAG) | Customer Memory (Mem0) |
|---|---|---|
| **Content** | Static policy documents | Dynamic per-customer history |
| **Added by** | You (file upload + ingest) | Automatically when drafts are accepted |
| **Scope** | Global (all customers) | Per customer email + per company |
| **Store** | `data/chroma_rag/` | `data/chroma_mem0/` |
| **Query** | Ticket text | Ticket text (same query, different store) |
| **Example** | "ATM daily limit is INR 25,000" | "Customer reported ATM issue at MG Road branch on Feb 2025, resolved within 24h" |

Both are injected into the system prompt simultaneously. The LLM sees:

```
Customer Memory Context:
- Customer reported ATM cash-not-dispensed on 2025-02-10, resolved by reversal.

Knowledge Base Context:
- [banking-atm-cash-withdrawal-faq.md] Reversal is typically processed within 24 hours...
```

---

## Troubleshooting

**"No relevant knowledge-base chunks found" in draft context**

The collection is empty. Ingest the knowledge base via the Streamlit sidebar.

**Irrelevant chunks being retrieved**

Your document sections are too long or too generic. Try shorter, more specific sections, or increase `RAG_CHUNK_OVERLAP` to retain more boundary context.

**"Gemini embedding initialization failed"**

`GOOGLE_API_KEY` is invalid or the `google-genai` package is missing. Run `uv sync` to ensure dependencies are installed, and verify the key at [aistudio.google.com](https://aistudio.google.com).

**Duplicate content after re-ingestion**

This should not happen because chunks are upserted by stable ID. If it does, ingest with `clear_existing: true` to rebuild from scratch.

**Wrong chunks retrieved after editing a document**

Old chunk IDs (based on old content hashes) are still in the collection. Ingest with `clear_existing: true`.
