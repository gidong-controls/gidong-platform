from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import controlstudio_host.app as app_module


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/api/v1/bootstrap")
    assert response.status_code == 200
    token = response.json()["csrfToken"]
    assert isinstance(token, str) and token
    return {"X-ControlStudio-CSRF": token}


def test_builtin_control_patterns_are_available_through_agent_derived_retrieval(
    client: TestClient,
) -> None:
    summary = client.get("/api/v1/knowledge/summary")
    assert summary.status_code == 200
    assert summary.json()["patternCount"] == 12
    assert summary.json()["retrievalMode"] == "agent_sidecar"

    result = client.get(
        "/api/v1/knowledge/search",
        params=[
            ("query", "모터 직입 기동"),
            ("vendor", "generic"),
            ("kinds", "control_pattern"),
        ],
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["results"]
    assert payload["results"][0]["kind"] == "control_pattern"
    assert payload["results"][0]["citation"]["knowledgeRef"].startswith("knowledge:")
    assert any("motor." in item["content"] for item in payload["results"])


def test_memory_requires_explicit_confirmation_before_search(
    client: TestClient,
) -> None:
    headers = _csrf_headers(client)
    proposed = client.post(
        "/api/v1/knowledge/memories",
        headers=headers,
        json={
            "title": "대동 모터 프로그램 구성 선호",
            "kind": "preference",
            "scope": "site:sample-line",
            "content": "모터 출력 명령은 출력 전용 프로그램에서 생성한다.",
            "vendor": "ls",
            "productFamily": "XGK",
            "version": "site-standard-1",
            "language": "ko",
        },
    )
    assert proposed.status_code == 201
    source = proposed.json()
    assert source["status"] == "proposed"
    assert (
        source["metadata"]["content"]
        == "모터 출력 명령은 출력 전용 프로그램에서 생성한다."
    )

    before = client.get(
        "/api/v1/knowledge/search",
        params={"query": "출력 전용 프로그램", "vendor": "ls"},
    )
    assert before.status_code == 200
    assert all(
        item["citation"]["sourceId"] != source["sourceId"] for item in before.json()["results"]
    )

    confirmed = client.post(
        f"/api/v1/knowledge/memories/{source['sourceId']}/confirm",
        headers=headers,
        json={"expectedRevision": source["revision"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmedBy"].startswith("browser:")
    assert confirmed.json()["metadata"]["content"] == source["metadata"]["content"]

    after = client.get(
        "/api/v1/knowledge/search",
        params={"query": "출력 전용 프로그램", "vendor": "ls"},
    )
    assert after.status_code == 200
    assert any(
        item["citation"]["sourceId"] == source["sourceId"] for item in after.json()["results"]
    )


def test_local_document_import_keeps_hash_page_and_vendor_scope(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    manual = tmp_path / "xg5000-test-manual.md"
    manual.write_text(
        "# XGK timer instructions\n\n"
        "The TMR instruction uses a time base selected for the applicable CPU "
        "and project configuration. Verify the current manual revision.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app_module,
        "select_knowledge_document_file",
        lambda: str(manual),
    )
    imported = client.post(
        "/api/v1/knowledge/import-local",
        headers=_csrf_headers(client),
        json={
            "title": "XG5000 test manual",
            "kind": "manual",
            "scope": "vendor:ls",
            "vendor": "ls",
            "productFamily": "XGK",
            "version": "test-revision",
            "language": "en",
            "authority": "user_imported_reference",
        },
    )
    assert imported.status_code == 200
    payload = imported.json()
    assert payload["selected"] is True
    assert payload["source"]["vendor"] == "ls"
    assert payload["source"]["productFamily"] == "XGK"
    assert payload["source"]["version"] == "test-revision"
    assert len(payload["source"]["sha256"]) == 64
    assert payload["pageCount"] == 1

    ls_result = client.get(
        "/api/v1/knowledge/search",
        params={
            "query": "TMR time base",
            "vendor": "ls",
            "kinds": "manual",
            "product_family": "XGK",
            "version": "test-revision",
        },
    )
    assert ls_result.status_code == 200
    hit = ls_result.json()["results"][0]
    assert hit["citation"]["page"] == 1
    assert hit["citation"]["locator"].endswith("#page=1")
    assert hit["authority"] == "user_imported_reference"

    other_vendor = client.get(
        "/api/v1/knowledge/search",
        params={
            "query": "TMR time base",
            "vendor": "mitsubishi",
            "kinds": "manual",
        },
    )
    assert other_vendor.status_code == 200
    assert other_vendor.json()["results"] == []


def test_local_import_cannot_self_assert_official_or_approved_authority(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/knowledge/import-local",
        headers=_csrf_headers(client),
        json={
            "kind": "manual",
            "scope": "vendor:ls",
            "vendor": "ls",
            "productFamily": "XGK",
            "version": "V2.5",
            "language": "en",
            "authority": "vendor_official",
        },
    )

    assert response.status_code == 422


def test_local_import_identity_includes_full_applicability(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    manual = tmp_path / "shared-manual.md"
    manual.write_text(
        "# Shared manual\n\nA sufficiently long shared manual paragraph for indexing.",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app_module,
        "select_knowledge_document_file",
        lambda: str(manual),
    )
    headers = _csrf_headers(client)
    base = {
        "kind": "manual",
        "scope": "vendor:ls",
        "vendor": "ls",
        "productFamily": "XGK",
        "version": "V2.5",
        "language": "en",
        "authority": "user_imported_reference",
    }

    first = client.post(
        "/api/v1/knowledge/import-local",
        headers=headers,
        json=base,
    )
    second = client.post(
        "/api/v1/knowledge/import-local",
        headers=headers,
        json={
            **base,
            "scope": "project:line-2",
            "productFamily": "XGI",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["source"]["sourceId"] != second.json()["source"]["sourceId"]
    assert second.json()["source"]["scope"] == "project:line-2"
    assert second.json()["source"]["productFamily"] == "XGI"
