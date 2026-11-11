"""Turn a document the model cannot see into text it can read.

The scope analysis sends content blocks to the model, and it could only ever
build two kinds: `image/*` and `application/pdf`. Everything else was fetched
from R2, found to be neither, and silently passed over — a ticked .docx
contributed exactly nothing and said nothing about it. On 880 Harbour four of
the seven available documents were in that position.

Extraction, not conversion. There is no LibreOffice in the API image and adding
one to render a Word file back to PDF would be a lot of machinery to recover
text that python-docx can read directly. What the model needs from a
specification or a room schedule is the words and the numbers, not the layout.

Deliberately NOT handled: legacy binary .doc and .xls. Those need antiword or a
full office suite, they are rare in this corpus (0 of 4,000+ documents at the
time of writing), and claiming support we do not have is how the silent drop
started. They are reported as unsupported, by name, with what to do instead.
"""

from __future__ import annotations

import csv
import io
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Per-document ceiling. A 200-page specification is real, and sending all of
#: it would crowd out the drawings that carry the quantities. Truncation is
#: always announced in the text handed to the model.
MAX_CHARS = 40_000
#: Across every text document in one scope run.
MAX_TOTAL_CHARS = 120_000
#: Spreadsheets fan out fast. One sheet of 5,000 rows is a data dump, not scope.
MAX_ROWS_PER_SHEET = 400
MAX_SHEETS = 20


@dataclass(frozen=True)
class Limits:
    chars: int = MAX_CHARS
    rows_per_sheet: int = MAX_ROWS_PER_SHEET
    sheets: int = MAX_SHEETS


#: The defaults above exist to keep a model prompt small. Search has no prompt
#: to protect: the 880 Harbour trial lost the back half of 5 of 6 estimate and
#: invoice spreadsheets to the 400-row cap. These are sized for searching.
SEARCH_LIMITS = Limits(chars=2_000_000, rows_per_sheet=20_000, sheets=100)
PROMPT_LIMITS = Limits()

TEXT_EXTS = (".txt", ".md", ".csv")
WORD_EXTS = (".docx",)
EXCEL_EXTS = (".xlsx", ".xlsm")
LEGACY_EXTS = (".doc", ".xls", ".ppt")

EXTRACTABLE_EXTS = TEXT_EXTS + WORD_EXTS + EXCEL_EXTS


@dataclass
class Extracted:
    text: str
    truncated: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


def _cap(text: str, lim: Limits = PROMPT_LIMITS) -> Extracted:
    if len(text) <= lim.chars:
        return Extracted(text=text)
    return Extracted(
        text=text[:lim.chars]
        + f"\n\n[... truncated at {lim.chars:,} characters of {len(text):,}]",
        truncated=True,
    )


def _from_docx(data: bytes, lim: Limits = PROMPT_LIMITS) -> Extracted:
    try:
        import docx  # python-docx
    except ImportError:  # pragma: no cover — dependency is pinned
        return Extracted(text="", error="python-docx is not installed")

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return Extracted(text="", error=f"could not be opened as a Word file ({exc})")

    parts: list[str] = [p.text.strip() for p in document.paragraphs if p.text.strip()]

    # Tables carry the schedules — door schedules, finish schedules, room
    # lists. Dropping them would lose the most quantity-dense part of the file.
    for t_i, table in enumerate(document.tables, start=1):
        rows: list[str] = []
        for row in table.rows[: lim.rows_per_sheet + 1]:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append(f"\n[Table {t_i}]\n" + "\n".join(rows))

    return _cap("\n".join(parts), lim)


def _from_xlsx(data: bytes, lim: Limits = PROMPT_LIMITS) -> Extracted:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover
        return Extracted(text="", error="openpyxl is not installed")

    try:
        # data_only: read the CACHED RESULT of a formula, not "=SUM(B2:B40)".
        # A spreadsheet of formulas tells the model nothing about quantities.
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        return Extracted(text="", error=f"could not be opened as a spreadsheet ({exc})")

    parts: list[str] = []
    truncated = False
    try:
        for name in wb.sheetnames[:lim.sheets]:
            ws = wb[name]
            rows: list[str] = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= lim.rows_per_sheet:
                    truncated = True
                    rows.append(f"[... more rows in '{name}' not shown]")
                    break
                cells = ["" if c is None else str(c).strip() for c in row]
                if any(cells):
                    rows.append(" | ".join(cells).rstrip(" |"))
            if rows:
                parts.append(f"[Sheet: {name}]\n" + "\n".join(rows))
        if len(wb.sheetnames) > lim.sheets:
            truncated = True
            parts.append(f"[... {len(wb.sheetnames) - lim.sheets} further sheets not shown]")
    finally:
        wb.close()

    result = _cap("\n\n".join(parts), lim)
    result.truncated = result.truncated or truncated
    return result


def _from_plain(data: bytes, ext: str, lim: Limits = PROMPT_LIMITS) -> Extracted:
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        raw = data.decode("latin-1", errors="replace")

    if ext != ".csv":
        return _cap(raw, lim)

    # Normalise a CSV into the same pipe-delimited shape the spreadsheet path
    # produces, so the model sees one table convention rather than two.
    try:
        rows = []
        for i, row in enumerate(csv.reader(io.StringIO(raw))):
            if i >= lim.rows_per_sheet:
                rows.append("[... more rows not shown]")
                break
            if any(c.strip() for c in row):
                rows.append(" | ".join(c.strip() for c in row))
        return _cap("\n".join(rows), lim)
    except csv.Error:
        return _cap(raw, lim)


def extract(filename: str, data: bytes, limits: Limits = PROMPT_LIMITS) -> Extracted:
    """Text from a document, or a stated reason there is none.

    ``limits`` defaults to the prompt-sized caps; search passes SEARCH_LIMITS.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in WORD_EXTS:
        return _from_docx(data, limits)
    if ext in EXCEL_EXTS:
        return _from_xlsx(data, limits)
    if ext in TEXT_EXTS:
        return _from_plain(data, ext, limits)
    if ext in LEGACY_EXTS:
        return Extracted(
            text="",
            error=(
                f"{ext} is the legacy binary format and cannot be read here — "
                "re-save it as the modern equivalent, or export to PDF"
            ),
        )
    return Extracted(text="", error=f"{ext or 'this file type'} cannot be read as text")


def budget(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim a set of extracted documents to the whole-run character ceiling.

    Earlier documents win. The office chose the order implicitly by what it
    ticked, and quietly dropping the middle of a 10-file selection would be the
    same silent loss this module exists to end — so anything cut says so.
    """
    total = 0
    out: list[dict[str, Any]] = []
    for item in items:
        text = item.get("text") or ""
        if total + len(text) <= MAX_TOTAL_CHARS:
            out.append(item)
            total += len(text)
            continue
        remaining = MAX_TOTAL_CHARS - total
        if remaining > 2_000:
            out.append({
                **item,
                "text": text[:remaining] + "\n\n[... truncated: run character budget reached]",
                "truncated": True,
            })
            total = MAX_TOTAL_CHARS
        else:
            out.append({**item, "text": "", "dropped": True,
                        "error": "not sent — the run's character budget was already full"})
    return out
