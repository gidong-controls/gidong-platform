from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path

MAX_KNOWLEDGE_FILE_BYTES = 100 * 1024 * 1024
MAX_KNOWLEDGE_PAGES = 5_000
MAX_EXTRACTED_CHARACTERS = 50_000_000
SUPPORTED_KNOWLEDGE_SUFFIXES = frozenset(
    {".pdf", ".md", ".markdown", ".txt", ".csv", ".tsv"}
)


class KnowledgeIngestionError(RuntimeError):
    """A local document could not be safely converted into searchable text."""


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    section: str | None = None
    locator_fragment: str | None = None


@dataclass(frozen=True)
class ExtractionReport:
    source_sha256: str
    file_name: str
    media_type: str
    page_count: int
    indexed_page_count: int
    low_text_pages: tuple[int, ...]
    extracted_characters: int
    status: str
    pages: tuple[ExtractedPage, ...]
    record_unit: str = "page"
    column_headers: tuple[str, ...] = ()


def extract_local_knowledge_document(path: Path) -> ExtractionReport:
    """Extract a user-selected engineering document or delimited I/O list.

    Page numbers are assigned by the ingestion loop and never inferred from PDF
    text. A scan-only PDF is rejected instead of silently creating an empty
    knowledge source.
    """

    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise KnowledgeIngestionError(
            "The selected knowledge source is no longer available."
        ) from exc
    if not resolved.is_file():
        raise KnowledgeIngestionError("The selected knowledge source is not a file.")
    suffix = resolved.suffix.casefold()
    if suffix not in SUPPORTED_KNOWLEDGE_SUFFIXES:
        raise KnowledgeIngestionError(
            "Only PDF, Markdown, plain-text, CSV, and TSV knowledge sources are supported."
        )
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise KnowledgeIngestionError(
            "The selected knowledge source could not be inspected."
        ) from exc
    if size <= 0:
        raise KnowledgeIngestionError("The selected knowledge source is empty.")
    if size > MAX_KNOWLEDGE_FILE_BYTES:
        raise KnowledgeIngestionError(
            "The selected knowledge source exceeds the 100 MB import limit."
        )

    try:
        raw = resolved.read_bytes()
        if len(raw) != size:
            raise KnowledgeIngestionError(
                "The selected knowledge source changed while it was being read."
            )
        digest = hashlib.sha256(raw).hexdigest()
        if suffix == ".pdf":
            return _extract_pdf(resolved, raw, digest)
        if suffix in {".csv", ".tsv"}:
            return _extract_delimited_file(resolved, raw, digest)
        return _extract_text_file(resolved, raw, digest)
    except KnowledgeIngestionError:
        raise
    except OSError as exc:
        raise KnowledgeIngestionError("The selected knowledge source could not be read.") from exc


def _extract_pdf(path: Path, raw: bytes, digest: str) -> ExtractionReport:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # Packaging failure.
        raise KnowledgeIngestionError("PDF support is not installed in the Host.") from exc

    try:
        reader = PdfReader(BytesIO(raw), strict=False)
    except Exception as exc:
        raise KnowledgeIngestionError("The selected PDF could not be opened.") from exc
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise KnowledgeIngestionError(
                "Encrypted PDF documents must be unlocked before import."
            ) from exc
        if not unlocked:
            raise KnowledgeIngestionError("Encrypted PDF documents must be unlocked before import.")
    page_count = len(reader.pages)
    if page_count == 0:
        raise KnowledgeIngestionError("The selected PDF has no pages.")
    if page_count > MAX_KNOWLEDGE_PAGES:
        raise KnowledgeIngestionError("The selected PDF exceeds the 5,000 page import limit.")

    pages: list[ExtractedPage] = []
    low_text_pages: list[int] = []
    extracted_characters = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except (TypeError, ValueError):
            # Some producer-specific PDFs do not support layout extraction.
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise KnowledgeIngestionError(
                    f"PDF text extraction failed on page {page_number}."
                ) from exc
        except Exception as exc:
            raise KnowledgeIngestionError(
                f"PDF text extraction failed on page {page_number}."
            ) from exc
        normalized = _normalize_text(text)
        if len(normalized) < 40:
            low_text_pages.append(page_number)
            continue
        extracted_characters += len(normalized)
        if extracted_characters > MAX_EXTRACTED_CHARACTERS:
            raise KnowledgeIngestionError("The selected document exceeds the extracted text limit.")
        pages.append(
            ExtractedPage(
                page_number=page_number,
                text=normalized,
                section=_first_heading(normalized),
            )
        )

    if not pages:
        raise KnowledgeIngestionError(
            "No searchable text was found. This PDF may require OCR before import."
        )
    status = "partial" if low_text_pages else "ready"
    return ExtractionReport(
        source_sha256=digest,
        file_name=path.name,
        media_type="application/pdf",
        page_count=page_count,
        indexed_page_count=len(pages),
        low_text_pages=tuple(low_text_pages),
        extracted_characters=extracted_characters,
        status=status,
        pages=tuple(pages),
    )


def _extract_text_file(path: Path, raw: bytes, digest: str) -> ExtractionReport:
    text = _decode_text(raw)
    normalized = _normalize_text(text)
    if not normalized:
        raise KnowledgeIngestionError("No searchable text was found.")
    if len(normalized) > MAX_EXTRACTED_CHARACTERS:
        raise KnowledgeIngestionError("The selected document exceeds the extracted text limit.")
    media_type = "text/markdown" if path.suffix.casefold() in {".md", ".markdown"} else "text/plain"
    return ExtractionReport(
        source_sha256=digest,
        file_name=path.name,
        media_type=media_type,
        page_count=1,
        indexed_page_count=1,
        low_text_pages=(),
        extracted_characters=len(normalized),
        status="ready",
        pages=(
            ExtractedPage(
                page_number=1,
                text=normalized,
                # The chunker tracks Markdown headings as they change. Passing
                # the first heading as an explicit section would incorrectly
                # label every later chunk with that first section.
                section=None,
            ),
        ),
    )


def _extract_delimited_file(path: Path, raw: bytes, digest: str) -> ExtractionReport:
    text = _decode_text(raw)
    delimiter = "\t" if path.suffix.casefold() == ".tsv" else _detect_csv_delimiter(text)
    try:
        reader = csv.reader(StringIO(text, newline=""), delimiter=delimiter, strict=True)
        header: list[str] | None = None
        pages: list[ExtractedPage] = []
        extracted_characters = 0
        for raw_row in reader:
            if not any(cell.strip() for cell in raw_row):
                continue
            row = [_normalize_table_cell(cell) for cell in raw_row]
            if header is None:
                header = row
                continue
            if len(pages) >= MAX_KNOWLEDGE_PAGES:
                raise KnowledgeIngestionError(
                    "The selected tabular source exceeds the 5,000 data-row import limit."
                )
            source_row = reader.line_num
            lines = [f"I/O list row {source_row}"]
            for column_index in range(1, max(len(header), len(row)) + 1):
                heading = header[column_index - 1] if column_index <= len(header) else ""
                label = heading or f"column {column_index}"
                value = row[column_index - 1] if column_index <= len(row) else ""
                lines.append(f"[{column_index}] {label}: {value}")
            normalized = "\n".join(lines)
            extracted_characters += len(normalized)
            if extracted_characters > MAX_EXTRACTED_CHARACTERS:
                raise KnowledgeIngestionError(
                    "The selected document exceeds the extracted text limit."
                )
            pages.append(
                ExtractedPage(
                    page_number=source_row,
                    text=normalized,
                    section=f"I/O list row {source_row}",
                    locator_fragment=f"row={source_row}",
                )
            )
    except csv.Error as exc:
        raise KnowledgeIngestionError(
            f"The selected {path.suffix.upper().lstrip('.')} file is malformed near row "
            f"{getattr(reader, 'line_num', 0) or 1}."
        ) from exc

    if header is None:
        raise KnowledgeIngestionError("The selected tabular source has no header row.")
    if not pages:
        raise KnowledgeIngestionError("The selected tabular source has no data rows.")
    media_type = (
        "text/tab-separated-values"
        if path.suffix.casefold() == ".tsv"
        else "text/csv"
    )
    return ExtractionReport(
        source_sha256=digest,
        file_name=path.name,
        media_type=media_type,
        page_count=len(pages),
        indexed_page_count=len(pages),
        low_text_pages=(),
        extracted_characters=extracted_characters,
        status="ready",
        pages=tuple(pages),
        record_unit="row",
        column_headers=tuple(header),
    )


def _decode_text(raw: bytes) -> str:
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp949", "cp932"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise KnowledgeIngestionError("The selected text document is not UTF-8, CP949, or CP932.")
    return text


def _detect_csv_delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:32_768], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _normalize_table_cell(value: str) -> str:
    return " ".join(value.replace("\x00", "").split())


def _normalize_text(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\x00", "").splitlines()]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if compact and not blank:
                compact.append("")
            blank = True
            continue
        compact.append(line)
        blank = False
    return "\n".join(compact).strip()


def _first_heading(text: str) -> str | None:
    for line in text.splitlines()[:30]:
        candidate = line.strip().lstrip("#").strip()
        if 3 <= len(candidate) <= 160:
            return candidate
    return None
