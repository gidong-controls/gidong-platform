from __future__ import annotations

from datetime import datetime, timezone

import pytest

from controlstudio_host.agent_client import SubprocessAgentClient
from controlstudio_host.agent_protocol import AgentProtocolMessage
from controlstudio_host.knowledge import KnowledgeStore
from controlstudio_host.model_connection import ModelSecrets, StoredModelConnection
from controlstudio_host.models import Message, MessageRole


def test_platform_source_snapshot_excludes_archived_documents(tmp_path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    active = store.import_text(
        title="Timer manual",
        content="Timer time-base semantics.",
        kind="manual",
        scope="global",
        vendor="ls",
        product_family="XGK",
        version="V2.5",
        authority="user-imported",
        revision="1",
    )
    first = store.agent_source_snapshot()
    assert first and first[0]["reference"] == f"knowledge://{active.source_id}"
    assert store.delete_source(active.source_id) is True
    assert store.agent_source_snapshot() == []


@pytest.mark.anyio
async def test_client_resyncs_agent_index_before_every_run_after_source_changes() -> None:
    class Broker:
        async def agent_tool_catalog(self):
            return []

    class Supervisor:
        def __init__(self) -> None:
            self.snapshots: list[list[dict]] = []

        async def sync_knowledge(self, documents):
            self.snapshots.append(list(documents))
            return len(documents)

        async def run(self, payload, *, on_message):
            await on_message(
                AgentProtocolMessage(
                    protocol_version="1.0",
                    type="run.final",
                    payload={
                        "run_id": payload["run_id"],
                        "output": {
                            "content": "ok",
                            "model": "mock",
                            "input_tokens": 0,
                            "output_tokens": 0,
                        },
                    },
                )
            )

        async def cancel_run(self, _run_id):
            return None

    settings = StoredModelConnection(
        protocol="openai_chat_completions",
        base_url="https://example.invalid/v1",
        model="mock",
        timeout_seconds=10,
        secret_ref="test",
        updated_at=datetime.now(timezone.utc),
        capability_verified_at=None,
        secrets=ModelSecrets(api_key="key", custom_headers={}),
    )
    supervisor = Supervisor()
    client = SubprocessAgentClient(settings, Broker(), supervisor)  # type: ignore[arg-type]
    snapshots = [
        [{"reference": "knowledge://source/old", "content": "old"}],
        [{"reference": "knowledge://source/new", "content": "new"}],
    ]

    async def source_snapshot():
        return snapshots.pop(0)

    client.bind_knowledge_snapshot(source_snapshot)
    history = [
        Message(
            id="message-1",
            conversation_id="conversation-1",
            sequence=1,
            role=MessageRole.USER,
            content="inspect",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )
    ]
    await client.run(prompt="inspect", history=history, conversation_id="conversation-1", run_id="run-1")
    await client.run(prompt="inspect", history=history, conversation_id="conversation-1", run_id="run-2")

    assert supervisor.snapshots == [
        [{"reference": "knowledge://source/old", "content": "old"}],
        [{"reference": "knowledge://source/new", "content": "new"}],
    ]
