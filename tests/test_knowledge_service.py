from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from customer_support_agent.core.settings import Settings
from customer_support_agent.integrations.rag.chroma_kb import KnowledgeBaseService


class _FakeEmbeddingFunction:
    """Deterministic fixed-dim embeddings — no model download required.

    Implements the minimal interface expected by chromadb (name, is_legacy).
    """

    _DIM = 64

    def name(self) -> str:  # required by chromadb validation
        return "default"

    def is_legacy(self) -> bool:  # suppresses DeprecationWarning
        return False

    def _embed(self, input: list[str]) -> list[list[float]]:
        result = []
        for text in input:
            seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
            vec = [((seed >> (i * 4)) & 0xF) / 15.0 for i in range(self._DIM)]
            result.append(vec)
        return result

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._embed(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self._embed(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self._embed(input)


@pytest.fixture()
def kb_service(tmp_path: Path) -> KnowledgeBaseService:
    settings = Settings(workspace_dir=tmp_path, google_api_key="")
    # Patch during __init__ so the collection is created with our fake EF.
    with patch.object(
        KnowledgeBaseService,
        "_build_embedding_function",
        return_value=_FakeEmbeddingFunction(),
    ):
        return KnowledgeBaseService(settings=settings)


def test_search_on_empty_collection_returns_empty_list(
    kb_service: KnowledgeBaseService,
) -> None:
    assert kb_service.search("refund policy") == []


def test_ingest_indexes_md_files(kb_service: KnowledgeBaseService, tmp_path: Path) -> None:
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "policy.md").write_text("Refunds are processed within 5 business days.")
    (kb_dir / "plans.md").write_text("Pro plan includes priority SLA of 8 hours.")

    stats = kb_service.ingest_directory(kb_dir)

    assert stats["files_indexed"] == 2
    assert stats["chunks_indexed"] >= 2
    assert stats["collection_count"] >= 2


def test_ingest_ignores_non_md_txt_files(
    kb_service: KnowledgeBaseService, tmp_path: Path
) -> None:
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "notes.md").write_text("Support process overview.")
    (kb_dir / "image.png").write_bytes(b"\x89PNG")  # should be ignored

    stats = kb_service.ingest_directory(kb_dir)

    assert stats["files_indexed"] == 1


def test_search_returns_results_after_ingestion(
    kb_service: KnowledgeBaseService, tmp_path: Path
) -> None:
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "billing.md").write_text(
        "All billing disputes must be raised within 30 days of the charge."
    )
    kb_service.ingest_directory(kb_dir)

    results = kb_service.search("billing dispute", top_k=2)

    assert isinstance(results, list)
    assert len(results) >= 1
    assert "content" in results[0]
    assert "source" in results[0]
    assert results[0]["source"] == "billing.md"


def test_ingest_with_clear_existing_resets_collection(
    kb_service: KnowledgeBaseService, tmp_path: Path
) -> None:
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "first.md").write_text("First document content.")
    kb_service.ingest_directory(kb_dir)
    count_before = kb_service._collection.count()

    (kb_dir / "first.md").write_text("Replacement content only.")
    kb_service.ingest_directory(kb_dir, clear_existing=True)
    count_after = kb_service._collection.count()

    assert count_after <= count_before  # cleared old chunks, re-indexed fresh
