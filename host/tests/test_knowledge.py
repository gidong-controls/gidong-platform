from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from controlstudio_host.knowledge import (
    KnowledgeConflict,
    KnowledgeKind,
    KnowledgeStatus,
    KnowledgeStore,
    PageRecord,
)


@pytest.fixture
def knowledge(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(tmp_path / "knowledge.sqlite3")


def test_platform_persists_lossless_page_payload_for_agent_snapshot(knowledge: KnowledgeStore) -> None:
    source = knowledge.import_pages(
        title="GX Works3 training manual",
        pages=[
            PageRecord(
                page=41,
                section="Interlock design",
                locator="manual.pdf#page=41",
                text="Forward and reverse commands must be mutually exclusive.",
                metadata={"chapter": "motor control"},
            )
        ],
        kind=KnowledgeKind.MANUAL,
        scope="vendor:mitsubishi",
        authority="vendor-official",
        vendor="Mitsubishi",
        product_family="MELSEC iQ-R",
        version="1.0",
        language="en",
        locator="manual.pdf",
    )

    document = knowledge.agent_source_snapshot()[0]
    assert document["source_id"] == source.source_id
    assert document["reference"] == f"knowledge://{source.source_id}?page=41&section=Interlock%20design"
    assert document["content"] == "Forward and reverse commands must be mutually exclusive."
    assert document["locator"] == "manual.pdf#page=41"
    assert document["metadata"]["chapter"] == "motor control"


def test_unconfirmed_memory_never_enters_agent_snapshot(knowledge: KnowledgeStore) -> None:
    proposed = knowledge.propose_memory(
        title="Motor style preference",
        content="Use a feedback-confirmed motor start pattern.",
        scope="project:line-7",
        proposed_by="engineer@example.com",
        kind=KnowledgeKind.PREFERENCE,
    )
    assert knowledge.agent_source_snapshot() == []

    confirmed = knowledge.confirm_memory(proposed.source_id, confirmed_by="lead")
    assert confirmed.status == KnowledgeStatus.CONFIRMED
    assert knowledge.agent_source_snapshot()[0]["source_id"] == proposed.source_id


def test_memory_confirmation_and_supersession_are_platform_lifecycle(knowledge: KnowledgeStore) -> None:
    previous = knowledge.propose_memory(
        title="Convention", content="Revision one", scope="workspace:line-1", proposed_by="lead"
    )
    previous = knowledge.confirm_memory(previous.source_id, confirmed_by="lead")
    replacement = knowledge.import_text(
        title="Convention", content="Revision two", kind=KnowledgeKind.MEMORY,
        scope="workspace:line-1", authority="user-proposed:lead", supersedes=previous.source_id,
        revision="2",
    )
    assert knowledge.get_source(previous.source_id).status == KnowledgeStatus.CONFIRMED
    knowledge.confirm_memory(replacement.source_id, confirmed_by="lead")
    assert knowledge.get_source(previous.source_id).status == KnowledgeStatus.SUPERSEDED
    assert [item["source_id"] for item in knowledge.agent_source_snapshot()] == [replacement.source_id]


def test_archive_removes_source_from_next_agent_sync_but_keeps_durable_authority(
    knowledge: KnowledgeStore,
) -> None:
    source = knowledge.import_text(
        title="Obsolete manual", content="XIC obsolete reference", kind=KnowledgeKind.MANUAL,
        scope="vendor:rockwell", authority="vendor-official", vendor="Rockwell",
        external_key="manual:obsolete-xic",
    )
    assert knowledge.agent_source_snapshot()
    assert knowledge.delete_source(source.source_id)
    assert knowledge.agent_source_snapshot() == []
    archived = knowledge.get_source(source.source_id)
    assert archived is not None and archived.status == KnowledgeStatus.ARCHIVED
    assert archived.external_key is None


def test_platform_has_no_derived_index_or_search_audit_after_new_import(
    knowledge: KnowledgeStore,
) -> None:
    knowledge.import_text(
        title="Manual", content="timer evidence", kind=KnowledgeKind.MANUAL,
        scope="vendor:ls", authority="user", vendor="ls",
    )
    with sqlite3.connect(knowledge.database_path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        }
    assert "knowledge_source_payloads" in names
    assert "knowledge_chunks" not in names
    assert "knowledge_fts_word" not in names
    assert "knowledge_fts_grams" not in names
    assert "knowledge_search_audit" not in names


def test_memory_cannot_be_confirmed_at_import_time(knowledge: KnowledgeStore) -> None:
    with pytest.raises(KnowledgeConflict):
        knowledge.import_text(
            title="Unsafe", content="must be proposed", kind=KnowledgeKind.MEMORY,
            scope="workspace:line-1", authority="user", status=KnowledgeStatus.CONFIRMED,
        )
