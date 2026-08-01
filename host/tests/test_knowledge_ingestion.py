from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from controlstudio_host.knowledge import KnowledgeKind, KnowledgeStore, PageRecord
from controlstudio_host.knowledge_ingestion import (
    KnowledgeIngestionError,
    extract_local_knowledge_document,
)

TEST_PDF_PASSWORD_VALUE = "customer-secret-password"


def _write_authored_pdf(
    path: Path,
    page_texts: list[str | None],
    *,
    password: str | None = None,
) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        stream = DecodedStreamObject()
        if text is None:
            stream.set_data(b"")
        else:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(
                (
                    "BT /F1 12 Tf 72 720 Td "
                    f"({escaped}) Tj ET"
                ).encode("ascii")
            )
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as output:
        writer.write(output)


def test_markdown_import_keeps_hash_and_locator(tmp_path: Path) -> None:
    source = tmp_path / "motor-standard.md"
    source.write_text(
        "# Motor control standard\n\n"
        "A run feedback timeout must be selected from machine evidence.\n",
        encoding="utf-8",
    )

    result = extract_local_knowledge_document(source)

    assert result.status == "ready"
    assert result.media_type == "text/markdown"
    assert result.page_count == 1
    assert result.pages[0].page_number == 1
    # Markdown heading transitions are resolved by KnowledgeStore's chunker,
    # rather than pinning every chunk to the document's first heading.
    assert result.pages[0].section is None
    assert len(result.source_sha256) == 64


def test_markdown_heading_survives_into_agent_source_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "line-seven-standard.md"
    source.write_text(
        "# Motor feedback policy\n\n"
        "The line seven motor feedback timeout requires measured machine evidence.\n",
        encoding="utf-8",
    )
    extracted = extract_local_knowledge_document(source)
    knowledge = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    imported = knowledge.import_pages(
        title="Line seven standard",
        pages=[
            PageRecord(
                page=page.page_number,
                text=page.text,
                section=page.section,
                locator=f"{extracted.file_name}#page={page.page_number}",
            )
            for page in extracted.pages
        ],
        kind=KnowledgeKind.PROJECT_DOCUMENT,
        scope="project:line-seven",
        authority="project-verified",
        vendor="LS",
        source_sha256=extracted.source_sha256,
    )

    snapshot = knowledge.agent_source_snapshot()

    assert [item["source_id"] for item in snapshot] == [imported.source_id]
    assert snapshot[0]["page"] == 1
    assert snapshot[0]["section"] == "Motor feedback policy"
    assert snapshot[0]["locator"] == "line-seven-standard.md#page=1"
    assert "page=1" in snapshot[0]["reference"]
    assert "section=Motor%20feedback%20policy" in snapshot[0]["reference"]


def test_cp949_text_import_preserves_korean_heading_and_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "motor-standard.txt"
    source.write_bytes(
        (
            "모터 운전 기준\n\n"
            "정회전 출력 뒤 운전 피드백과 과부하 접점을 확인한다.\n"
        ).encode("cp949")
    )

    result = extract_local_knowledge_document(source)

    assert result.status == "ready"
    assert result.media_type == "text/plain"
    assert result.pages[0].section is None
    assert "운전 피드백과 과부하 접점" in result.pages[0].text


def test_pdf_multi_page_extraction_preserves_page_numbers_and_marks_partial(
    tmp_path: Path,
) -> None:
    source = tmp_path / "authored-motor-manual.pdf"
    _write_authored_pdf(
        source,
        [
            (
                "Motor start permissive and overload feedback requirements "
                "must be verified before commissioning."
            ),
            "scan",
            (
                "Motor stop timeout and restart recovery requirements must "
                "be validated with measured machine evidence."
            ),
        ],
    )

    result = extract_local_knowledge_document(source)

    assert result.status == "partial"
    assert result.media_type == "application/pdf"
    assert result.page_count == 3
    assert result.indexed_page_count == 2
    assert result.low_text_pages == (2,)
    assert [page.page_number for page in result.pages] == [1, 3]
    assert "overload feedback" in result.pages[0].text
    assert "restart recovery" in result.pages[1].text


def test_scan_only_pdf_is_rejected_instead_of_creating_empty_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan-only.pdf"
    _write_authored_pdf(source, [None, None])

    with pytest.raises(KnowledgeIngestionError, match="require OCR"):
        extract_local_knowledge_document(source)


def test_password_encrypted_pdf_is_rejected_without_leaking_password(
    tmp_path: Path,
) -> None:
    source = tmp_path / "encrypted-manual.pdf"
    _write_authored_pdf(
        source,
        [
            (
                "Encrypted motor manual content must never be indexed "
                "without an explicitly unlocked source."
            )
        ],
        password=TEST_PDF_PASSWORD_VALUE,
    )

    with pytest.raises(
        KnowledgeIngestionError,
        match="must be unlocked",
    ) as failure:
        extract_local_knowledge_document(source)

    assert "customer-secret-password" not in str(failure.value)


def test_scan_or_blank_text_is_not_silently_indexed(tmp_path: Path) -> None:
    source = tmp_path / "blank.txt"
    source.write_text("\n\n", encoding="utf-8")

    with pytest.raises(KnowledgeIngestionError, match="No searchable text"):
        extract_local_knowledge_document(source)


def test_unsupported_document_type_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "manual.exe"
    source.write_bytes(b"not a manual")

    with pytest.raises(KnowledgeIngestionError, match="Only PDF"):
        extract_local_knowledge_document(source)


def test_disappeared_selected_file_returns_sanitized_ingestion_error(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "customer-secret-project" / "manual.md"

    with pytest.raises(
        KnowledgeIngestionError,
        match="no longer available",
    ) as failure:
        extract_local_knowledge_document(missing)

    assert str(missing) not in str(failure.value)
