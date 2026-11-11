"""Searching inside documents: reading their text page by page (migration 0068)
and finding it with Postgres full-text search.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.document_text import DocumentText  # noqa: E402
from app.routers import documents as R  # noqa: E402
from app.services import document_text_index as T  # noqa: E402
from app.services.document_search import HIT_END, HIT_START, search_document_pages  # noqa: E402


def _pdf(*pages: str) -> bytes:
    import fitz

    pdf = fitz.open()
    for body in pages:
        page = pdf.new_page()
        if body:
            page.insert_text((72, 72), body)
    return pdf.tobytes()


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'Text',:s,'lead')"),
               {"i": str(pid), "s": f"tx-{str(pid)[:8]}"})
    return pid


def _su(db):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'superadmin')"),
               {"i": str(uid), "e": f"{uid}@t.local"})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role="superadmin")


def _doc(db, pid, name="spec.pdf", size=100, archived=False, category="spec"):
    d = Document(project_id=pid, category=category, phase="preconstruction", filename=name,
                 r2_key=f"k/{uuid4()}", mime="application/pdf", size_bytes=size,
                 archived=archived)
    db.add(d)
    db.flush()
    return d


def _read(db, doc, data: bytes) -> str:
    assert T.queue_document_text(db, doc)
    db.flush()
    return T.read_document(db, doc.id, fetch=lambda key: data)


# ── reading ───────────────────────────────────────────────────────────────

def test_a_pdf_is_stored_page_by_page_and_blank_pages_are_skipped():
    out = T.extract_pages("spec.pdf", _pdf("Section 08 Windows triple glazed",
                                           "", "Doors are solid core fir"))
    assert out.status == "ok" and out.page_count == 3
    assert [n for n, _ in out.pages] == [1, 3]


def test_a_scan_has_no_text_and_says_why():
    out = T.extract_pages("scan.pdf", _pdf("", "Page 2"))
    assert out.status == "no_text" and "scan" in out.error and not out.pages


def test_a_short_sheet_title_is_kept_when_the_file_has_text():
    out = T.extract_pages("set.pdf", _pdf("General notes for the whole drawing set", "A-201"))
    assert [n for n, _ in out.pages] == [1, 2]


def test_a_damaged_pdf_fails_with_a_reason():
    out = T.extract_pages("bad.pdf", b"not a pdf at all")
    assert out.status == "failed" and out.error


def test_word_excel_and_text_are_one_page():
    out = T.extract_pages("notes.txt", b"Anchor bolts at 8 inch embedment")
    assert out.status == "ok" and out.pages == [(1, "Anchor bolts at 8 inch embedment")]


def test_nul_characters_are_removed():
    assert "\x00" not in T._clean("a\x00b  c")


# ── queue ─────────────────────────────────────────────────────────────────

def test_photos_are_never_queued(db_session):
    d = _doc(db_session, _project(db_session), "IMG_0001.jpg")
    assert T.queue_document_text(db_session, d) is False
    assert db_session.get(DocumentText, d.id) is None


def test_queueing_twice_leaves_one_row_and_force_reads_again(db_session):
    pid = _project(db_session)
    d = _doc(db_session, pid)
    _read(db_session, d, _pdf("Hello framing schedule"))
    assert db_session.get(DocumentText, d.id).status == "ok"
    assert T.queue_document_text(db_session, d) is False
    assert T.queue_document_text(db_session, d, force=True) is True
    assert db_session.get(DocumentText, d.id).status == "pending"


def test_read_stores_status_counts_and_replaces_old_pages(db_session):
    pid = _project(db_session)
    d = _doc(db_session, pid)
    assert _read(db_session, d, _pdf("first version text here", "second page text here")) == "ok"
    row = db_session.get(DocumentText, d.id)
    assert (row.page_count, row.pages_with_text) == (2, 2) and row.char_count > 0
    T.queue_document_text(db_session, d, force=True)
    T.read_document(db_session, d.id, fetch=lambda k: _pdf("only one page now present"))
    n = db_session.execute(_T("select count(*) from document_text_pages where document_id=:d"),
                           {"d": d.id}).scalar()
    assert n == 1


def test_too_large_is_not_fetched(db_session):
    d = _doc(db_session, _project(db_session), size=T.MAX_BYTES + 1)
    T.queue_document_text(db_session, d)
    db_session.flush()

    def boom(key):
        raise AssertionError("should not fetch")

    assert T.read_document(db_session, d.id, fetch=boom) == "too_large"


def test_a_storage_failure_is_recorded_not_raised(db_session):
    d = _doc(db_session, _project(db_session))
    T.queue_document_text(db_session, d)
    db_session.flush()

    def gone(key):
        raise RuntimeError("NoSuchKey")

    assert T.read_document(db_session, d.id, fetch=gone) == "failed"
    assert "storage" in db_session.get(DocumentText, d.id).error


def test_claim_takes_pending_and_retries_a_stale_claim_then_gives_up(db_session):
    d = _doc(db_session, _project(db_session))
    T.queue_document_text(db_session, d)
    db_session.flush()
    # Only this test's row should be claimable; park any others.
    db_session.execute(_T("update document_texts set status='ok' where document_id <> :d"),
                       {"d": d.id})
    assert T.claim_next(db_session) == d.id
    row = db_session.get(DocumentText, d.id)
    db_session.refresh(row)
    assert row.status == "running" and row.attempts == 1
    assert T.claim_next(db_session) is None  # still fresh
    db_session.execute(_T("update document_texts set claimed_at = now() - interval '1 hour' "
                          "where document_id=:d"), {"d": d.id})
    assert T.claim_next(db_session) == d.id
    db_session.execute(_T("update document_texts set claimed_at = now() - interval '1 hour', "
                          "attempts = 3 where document_id=:d"), {"d": d.id})
    assert T.claim_next(db_session) is None
    db_session.refresh(row)
    assert row.status == "failed" and "3 times" in row.error


# ── search ────────────────────────────────────────────────────────────────

def test_search_finds_a_stemmed_word_and_cites_the_page(db_session):
    pid = _project(db_session)
    d = _doc(db_session, pid, "Spec book.pdf")
    _read(db_session, d, _pdf("General conditions", "All window frames are fibreglass"))
    hits = search_document_pages(db_session, query="windows", project_ids=[pid])
    assert [h["id"] for h in hits] == [str(d.id)]
    assert hits[0]["page"] == 2 and hits[0]["matched_on"] == "found on page 2"
    assert f"{HIT_START}window{HIT_END}" in hits[0]["snippet"]


def test_search_counts_matching_pages_and_ranks_by_them(db_session):
    pid = _project(db_session)
    once = _doc(db_session, pid, "a.pdf")
    many = _doc(db_session, pid, "b.pdf")
    _read(db_session, once, _pdf("joist hanger detail at the beam line"))
    _read(db_session, many, _pdf("joist spacing is sixteen inches on centre", "joist size is two by ten", "unrelated cover sheet text"))
    hits = search_document_pages(db_session, query="joist", project_ids=[pid])
    assert [h["id"] for h in hits] == [str(many.id), str(once.id)]
    assert hits[0]["pages_matched"] == 2


def test_word_files_do_not_cite_a_page(db_session):
    pid = _project(db_session)
    d = _doc(db_session, pid, "minutes.txt")
    _read(db_session, d, b"Site meeting about the retaining wall")
    hit = search_document_pages(db_session, query="retaining wall", project_ids=[pid])[0]
    assert hit["page"] is None and hit["matched_on"] == "found in the text"


def test_search_never_crosses_projects_or_shows_archived(db_session):
    mine, other = _project(db_session), _project(db_session)
    a = _doc(db_session, other, "theirs.pdf")
    b = _doc(db_session, mine, "old.pdf")
    _read(db_session, a, _pdf("membrane flashing at every window head"))
    _read(db_session, b, _pdf("membrane flashing at every window head"))
    b.archived = True
    db_session.flush()
    assert search_document_pages(db_session, query="membrane", project_ids=[mine]) == []
    assert search_document_pages(db_session, query="membrane", project_ids=[]) == []


def test_endpoint_lists_a_name_match_once_and_reports_coverage(db_session):
    pid, su = _project(db_session), _su(db_session)
    named = _doc(db_session, pid, "Truss layout.pdf")
    inside = _doc(db_session, pid, "Engineer letter.pdf")
    waiting = _doc(db_session, pid, "Later.pdf")
    _read(db_session, named, _pdf("truss layout sheet for the roof framing"))
    _read(db_session, inside, _pdf("the truss supplier confirmed"))
    T.queue_document_text(db_session, waiting)
    db_session.flush()
    out = R.search_documents_endpoint(q="truss", project_id=pid, category=None, limit=25,
                                      db=db_session, user=su)
    ids = [r["id"] for r in out["results"]]
    assert ids == [str(named.id), str(inside.id)]
    assert out["results"][1]["matched_on"] == "found on page 1"
    cov = out["text_coverage"]
    assert (cov["readable"], cov["searchable"], cov["waiting"]) == (3, 2, 1)


def test_text_status_endpoint_and_read_again(db_session):
    pid, su = _project(db_session), _su(db_session)
    d = _doc(db_session, pid, "scan.pdf")
    _read(db_session, d, _pdf(""))
    rows = R.documents_text_status(project_id=pid, db=db_session, user=su)
    assert rows[0]["status"] == "no_text" and rows[0]["error"] == T.NO_TEXT_PDF
    R.read_document_text_again(document_id=d.id, db=db_session, user=su)
    assert db_session.get(DocumentText, d.id).status == "pending"


# ── limits (raised after the 880 Harbour trial) ────────────────────────────

def _xlsx(rows: int) -> bytes:
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for i in range(rows):
        ws.append([f"line {i}", i])
    ws.append(["final row says hydronic baseboard", 1])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_search_reads_a_whole_spreadsheet_past_the_prompt_row_cap():
    out = T.extract_pages("Estimate.xlsx", _xlsx(1_000))
    assert out.status == "ok" and not out.truncated
    assert "hydronic baseboard" in out.pages[0][1]


def test_the_prompt_caps_are_unchanged_for_the_ai():
    from app.services import doc_text

    got = doc_text.extract("Estimate.xlsx", _xlsx(1_000))
    assert got.truncated and "hydronic baseboard" not in got.text


def test_a_dense_notes_page_is_kept_whole():
    body = " ".join(f"note{i}" for i in range(6_000))  # ~50k characters
    import fitz

    pdf = fitz.open()
    page = pdf.new_page(width=3000, height=3000)
    page.insert_textbox(fitz.Rect(10, 10, 2990, 2990), body, fontsize=4)
    out = T.extract_pages("notes.pdf", pdf.tobytes())
    assert len(out.pages[0][1]) > 20_000 and not out.truncated
