"""
CompanyFactEmbedding 嘅 CRUD + content_hash-based dedup 查詢。
"""
from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select

from app.models import CompanyFactEmbedding


class EmbeddingManagerMixin:
    def get_all_fact_embedding_hashes(self) -> dict[tuple[str, int], str]:
        """一次過攞晒現存所有 embedding 嘅 (entity_type, entity_id) -> content_hash。

        俾 embed_all_facts() 呢類 batch job 用嚟做「呢個 entity 有冇變過」嘅
        pre-check——一次 SQL 查晒晒(淨係 3 條窄欄位，唔夾 embedding vector)，
        喺 Python 層面同新計嘅 content_hash 比對，唔使 loop 入面逐個 entity 都
        開一次 get_fact_embedding() 嘅 DB round-trip。"""
        with self.session_scope() as s:
            stmt = select(
                CompanyFactEmbedding.entity_type,
                CompanyFactEmbedding.entity_id,
                CompanyFactEmbedding.content_hash,
            )
            return {(entity_type, entity_id): content_hash for entity_type, entity_id, content_hash in s.execute(stmt)}

    def get_fact_embedding(self, entity_type: str, entity_id: int) -> Optional[CompanyFactEmbedding]:
        """攞返呢個 entity 現有嘅 embedding row（有嘅話），用嚟同新嘅 content_hash 比對，
        判斷 description 有冇變過。"""
        with self.session_scope() as s:
            stmt = select(CompanyFactEmbedding).where(
                CompanyFactEmbedding.entity_type == entity_type,
                CompanyFactEmbedding.entity_id == entity_id,
            )
            return s.scalars(stmt).first()

    def find_embedding_by_hash(self, content_hash: str) -> Optional[list[float]]:
        """搵下有冇任何一行（可以係唔同 entity）已經有一樣嘅 content_hash——
        有就可以直接攞返個現成 embedding vector 嚟用，唔使再 call embedding API。"""
        with self.session_scope() as s:
            stmt = select(CompanyFactEmbedding.embedding).where(
                CompanyFactEmbedding.content_hash == content_hash
            ).limit(1)
            return s.scalars(stmt).first()

    def upsert_fact_embedding(
        self,
        *,
        entity_type: str,
        entity_id: int,
        company_id: Optional[int],
        content_text: str,
        content_hash: str,
        embedding: Sequence[float],
        embedding_model: str,
    ) -> CompanyFactEmbedding:
        with self.session_scope() as s:
            existing = s.scalars(
                select(CompanyFactEmbedding).where(
                    CompanyFactEmbedding.entity_type == entity_type,
                    CompanyFactEmbedding.entity_id == entity_id,
                )
            ).first()
            if existing is not None:
                existing.content_text = content_text
                existing.content_hash = content_hash
                existing.embedding = embedding
                existing.embedding_model = embedding_model
                s.flush()
                return existing

            row = CompanyFactEmbedding(
                entity_type=entity_type,
                entity_id=entity_id,
                company_id=company_id,
                content_text=content_text,
                content_hash=content_hash,
                embedding=embedding,
                embedding_model=embedding_model,
            )
            s.add(row)
            s.flush()
            return row
