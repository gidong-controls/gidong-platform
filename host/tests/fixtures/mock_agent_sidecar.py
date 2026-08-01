"""Tiny stdlib Agent Protocol v1 mock used by public Platform tests."""

import json
import sys

documents: list[dict] = []


def send(message_type: str, payload: dict) -> None:
    sys.stdout.write(
        json.dumps(
            {"protocol_version": "1.0", "type": message_type, "payload": payload},
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    if message["type"] == "initialize":
        send(
            "initialized",
            {
                "manifest": {
                    "protocol_version": "1.0",
                    "agent_id": "controlstudio-mock-agent",
                    "capabilities": ["health", "shutdown"],
                }
            },
        )
    elif message["type"] == "health":
        send("health.result", {"status": "ready", "agent_id": "controlstudio-mock-agent"})
    elif message["type"] == "knowledge.sync":
        documents = message["payload"].get("documents", [])
        send("knowledge.synced", {"count": len(documents)})
    elif message["type"] == "knowledge.search":
        payload = message["payload"]
        query = str(payload.get("query", "")).casefold()
        vendor = payload.get("vendor")
        kinds = set(payload.get("kinds") or [])
        scopes = set(payload.get("scopes") or [])
        product_family = payload.get("product_family")
        version = payload.get("version")
        results = []
        for ordinal, document in enumerate(documents):
            if vendor and document.get("vendor") and document["vendor"].casefold() != vendor.casefold():
                continue
            if kinds and document.get("kind") not in kinds:
                continue
            if scopes and document.get("scope") not in scopes:
                continue
            if product_family and document.get("product_family") != product_family:
                continue
            if version and document.get("version") != version:
                continue
            if query and not any(term in str(document.get("content", "")).casefold() for term in query.split()):
                continue
            results.append(
                {
                    "knowledge_ref": document["reference"],
                    "chunk_id": f"mock-{ordinal:060d}",
                    "source_id": document["source_id"],
                    "content": document["content"],
                    "title": document["title"],
                    "kind": document["kind"],
                    "scope": document["scope"],
                    "vendor": document.get("vendor"),
                    "product_family": document.get("product_family"),
                    "version": document.get("version"),
                    "language": document.get("language"),
                    "authority": document["authority"],
                    "locator": document.get("locator"),
                    "page": document.get("page"),
                    "section": document.get("section"),
                    "metadata": document.get("metadata", {}),
                }
            )
        send("knowledge.search.result", {"results": results, "untrusted_reference_data": True})
    elif message["type"] == "shutdown":
        send("shutdown.complete", {"status": "stopped"})
        break
