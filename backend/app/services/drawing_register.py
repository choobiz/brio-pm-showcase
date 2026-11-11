"""Work out which drawings are the same sheet, and which revision is current.

⚠️ THE PROBLEM IS NOT THE ONE IT LOOKS LIKE. "Documents have no revision chain"
suggests the fix is a revision column. Measured against the real set, the first
obstacle is that a large share of the 142 drawings are DUPLICATES, and the
revision relationships are buried under them:

    STRUCTURAL PLAN-6 NOV 2025-1234 EXAMPLE WAY…      386,732 bytes
    STRUCTURAL_PLAN-6_NOV_2025-1234_EXAMPLE_WAY…      386,732 bytes   <- same file
    Structural Plan-28 Oct 2025-1234 Example Way…     392,338 bytes   <- the real prior revision

    Copy of <X> vs <X>                                  same size, 6 of 6
    105-107 … Erosion Control Plan.pdf                  3 identical copies

So: collapse duplicates first, THEN group what remains by sheet identity. Doing
it the other way round reports two revisions of a drawing that was uploaded
twice, which is worse than reporting nothing.

NOTHING HERE WRITES. It produces a PROPOSAL for a person to confirm, because a
filename is evidence and not a fact: "R2" in a name is what someone typed, and
a set where the revision only exists in prose cannot be made authoritative by
parsing it harder. Confidence is returned alongside every grouping so the
interface can show what it is unsure about.
"""

from __future__ import annotations

import re
from typing import Any

# ── tokens that identify a REVISION ───────────────────────────────────────
#: "(R2)", "_R2_", " R3 " — a letter R followed by digits, as its own token.
_REV_R = re.compile(r"(?<![a-z0-9])r(\d{1,2})(?![a-z0-9])", re.I)
#: "Rev C", "RevE", "-Rev-B", "rev.D"
_REV_WORD = re.compile(r"(?<![a-z])rev[\s._-]*([a-z])(?![a-z])", re.I)
#: Preliminary / not-for-construction markers. Treated as a revision LABEL, not
#: as a date, because "PRELIM" orders before any issued revision.
_PRELIM = re.compile(r"(?<![a-z])(prelim\w*|draft|wip|not for construction|nfc)(?![a-z])", re.I)

# ── tokens that identify an ISSUE DATE ────────────────────────────────────
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec")
#: "28 Oct 2025", "6 NOV 2025"
_DATE_DMY = re.compile(
    r"(?<!\d)(\d{1,2})[\s._-]+(" + "|".join(_MONTHS) + r")\w*[\s._-]+(\d{4})(?!\d)", re.I)
#: "2021.08.12", "2020-03-15"
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})[._-](\d{1,2})[._-](\d{1,2})(?!\d)")
#: "20230123" — eight digits. Ordered last because it is the most collidable.
_DATE_COMPACT = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")

# ── the sheet number, where the drawing carries one ───────────────────────
#: "Sheet - A14 - BUILDING SECTIONS", "Sheet_-_A1-04_-_Site_Plan"
_SHEET_LABELLED = re.compile(r"sheet[\s._-]+([a-z]{1,3}[\s._-]?\d{1,3}(?:[.\-_]\d{1,3})?)[\s._-]",
                             re.I)

#: A drawing exported as both DWG and DXF is ONE sheet, not two revisions.
#: DXF wins, matching drawing_analysis._dedupe_formats — the DWG path converts
#: to DXF anyway, and the conversion is the step that fails. Keeping the two
#: rules in step matters: if the register says "revision" where the takeoff says
#: "duplicate format", one of them is telling the estimator something false.
_CAD_FORMATS = (".dxf", ".dwg")

#: Upload artefacts that carry no meaning: "Copy of ", trailing "_1_", "(1)".
_COPY_PREFIX = re.compile(r"^\s*copy\s+of\s+", re.I)
#: One or more trailing "_1_" / "(2)" marks, matched together and anchored to
#: the end. Matching them one at a time does not work: the leading character
#: class eats the previous mark's closing delimiter, so " _1_ _1_" collapses to
#: a stray "_1" that no further pass can remove.
#: "(R2)" is untouched — a letter follows the bracket, so the digit class fails.
_DOWNLOAD_SUFFIX = re.compile(r"(?:[\s._-]*[_(]\s*\d{1,2}\s*[_)])+\s*$")


def _strip_ext(name: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)(\.[a-z0-9]{1,5})?$", name.strip(), re.I)
    return (m.group(1) or "", (m.group(2) or "").lower())


def _strip_artifacts(filename: str) -> str:
    """Remove the extension and the marks of having been downloaded twice."""
    stem, _ext = _strip_ext(filename or "")
    s = _COPY_PREFIX.sub("", stem)
    return _DOWNLOAD_SUFFIX.sub("", s).strip()


def _key_form(s: str) -> str:
    """Letters and digits only.

    ⚠️ Deliberately this aggressive, and measured against the real set rather
    than chosen for tidiness. Two uploads of one structural plan differ by
    commas AND by "B.C." against "BC" — punctuation and spacing are simply not
    stable across exports, so a key that respects them reports two drawings
    where there is one. What survives is compared, never displayed.
    """
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def normalise(filename: str) -> str:
    """A filename reduced to what it actually says, for comparison only."""
    return _key_form(_strip_artifacts(filename))


def extract_revision(filename: str) -> dict[str, Any] | None:
    """The revision this filename claims, or None.

    Returns the token as written so a person can check it against the drawing,
    plus a sort key. A parsed revision is a CLAIM about a file, never a fact
    about a drawing.
    """
    stem, _ = _strip_ext(filename or "")
    s = re.sub(r"[_]+", " ", stem)

    m = _REV_WORD.search(s)
    if m:
        letter = m.group(1).upper()
        return {"label": f"Rev {letter}", "token": m.group(0).strip(),
                "sort": (2, ord(letter)), "kind": "letter"}
    m = _REV_R.search(s)
    if m:
        n = int(m.group(1))
        return {"label": f"R{n}", "token": m.group(0).strip(),
                "sort": (2, n), "kind": "numbered"}
    m = _PRELIM.search(s)
    if m:
        # Sorts BELOW any issued revision: preliminary is never current if an
        # issued one exists.
        return {"label": m.group(1).upper(), "token": m.group(0).strip(),
                "sort": (0, 0), "kind": "preliminary"}
    return None


def extract_issue_date(filename: str) -> dict[str, Any] | None:
    """The date this filename claims, as (y, m, d) plus the text it came from."""
    stem, _ = _strip_ext(filename or "")
    s = re.sub(r"[_]+", " ", stem)

    m = _DATE_DMY.search(s)
    if m:
        y, mo, d = int(m.group(3)), _MONTHS.index(m.group(2)[:3].lower()) + 1, int(m.group(1))
        return {"date": (y, mo, d), "token": m.group(0).strip()}
    m = _DATE_YMD.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return {"date": (y, mo, d), "token": m.group(0).strip()}
    m = _DATE_COMPACT.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return {"date": (y, mo, d), "token": m.group(0).strip()}
    return None


def extract_sheet_number(filename: str) -> str | None:
    """"Sheet - A14 - BUILDING SECTIONS" -> "A14".

    Only the LABELLED form is read. A bare "A14" anywhere in a filename is as
    likely to be part of an address or a job number, and a wrong sheet number
    silently merges two unrelated drawings — worse than having none.
    """
    stem, _ = _strip_ext(filename or "")
    m = _SHEET_LABELLED.search(re.sub(r"[_]+", " ", stem) + " ")
    if not m:
        return None
    return re.sub(r"[\s._-]+", "", m.group(1)).upper()


def sheet_key(filename: str) -> str:
    """What identifies the DRAWING, with the revision and date removed.

    This is the join: "Structural Plan-28 Oct 2025-1234 Example Way" and
    "STRUCTURAL PLAN-6 NOV 2025-1234 EXAMPLE WAY" differ only by their date
    token, so both reduce to the same key and become two revisions of one sheet.
    """
    # ⚠️ THE SHEET NUMBER IS NOT THE KEY, and this was learned the hard way.
    #
    # Keying on it merged three buildings. 105/107 Maple is a multi-lot
    # site, so the set contains "107 Maple … Sheet - A2 - SITE PLAN",
    # "109 Maple … Sheet - A2 - SITE PLAN" and "111 Maple … Sheet - A2
    # - SITE PLAN" — three different buildings that share a numbering scheme.
    # Keyed on "A2" they became one drawing with two superseded revisions, and
    # four of the eight histories found across the whole set were this mistake.
    #
    # A sheet number is only unique within an originator, and nothing in a
    # filename reliably says who that is. So the NAME is the key — it carries
    # the building prefix — and the sheet number is reported alongside.
    #
    # This will miss a reissue that also renames the file. A miss is the safe
    # failure: reporting two drawings where there is one chain is conservative,
    # whereas merging three buildings is confidently wrong.
    # Tokens are removed BEFORE the key form: once punctuation and spaces are
    # gone, "28 Oct 2025" is no longer findable in "28oct2025theplan".
    # ⚠️ Underscores become spaces FIRST, matching what the extractors do.
    # Without it the token is found in one form and removed from another: a date
    # reads as "6 NOV 2025" but the raw name says "6_NOV_2025", the replace
    # misses, and the date stays in the key. The underscore-mangled export of a
    # drawing then never groups with its own revisions — silently, because the
    # file still appears, just alone.
    s = re.sub(r"[_]+", " ", _strip_artifacts(filename))
    for extractor in (extract_revision, extract_issue_date):
        found = extractor(filename)
        if found:
            s = re.sub(re.escape(found["token"]), " ", s, flags=re.I)
    return f"name:{_key_form(s)}"


# ── assembling the register ───────────────────────────────────────────────

#: How far to trust a grouping, and why. Shown, not hidden.
CONFIDENCE_REASON = {
    "revision_token": "the revision is written on the filename",
    "date_token": "the issue date is written on the filename",
    "name_only": "grouped by name alone — no sheet number, revision or date found",
}


def _tidiness(doc: dict[str, Any]) -> tuple:
    """Sort key for choosing which of several identical files to keep.

    ⚠️ Keeping whichever arrived first showed "Copy of 109 Maple_Issued for
    coordination…" as the CURRENT revision while the original sat beside it
    marked superseded. Backwards, and worse, it changed with the order the rows
    were read — the same set could answer differently on two runs.

    So the tidiest name wins, deterministically: no "Copy of" prefix, then no
    "_1_" download mark, then the shorter name, then the earliest upload.
    """
    fn = doc.get("filename") or ""
    stem = _strip_ext(fn)[0]
    # Underscores before length. Length alone picked the underscore-mangled
    # variant of a real pair, because replacing ", " and " - " with "_" makes
    # the mangled name SHORTER:
    #   1234 Example Way, North Vancouver - Garage & Office … SMP (R2) …
    #   1234_Example_Way_North_Vancouver_-_Garage_Office_…_SMP_R2_…   <- shorter
    # A name full of underscores is almost always the export-mangled copy, so
    # the one with fewer of them is the one a person should be shown.
    return (
        1 if _COPY_PREFIX.search(fn) else 0,
        1 if _DOWNLOAD_SUFFIX.search(stem) else 0,
        stem.count("_"),
        len(fn),
        str(doc.get("created_at") or ""),
        str(doc.get("id") or ""),
    )


def _dup_key(doc: dict[str, Any]) -> tuple:
    """Same normalised name AND same byte size = the same file twice.

    Size alone would collide; name alone would merge genuine revisions. Together
    they were right on every pair checked in the real set — including six
    "Copy of X" pairs that matched X to the byte.
    """
    return (normalise(doc.get("filename") or ""), doc.get("size_bytes"))


def build_drawing_register(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Group a project's drawings into sheets, each with its revisions.

    ``documents`` are dicts with at least id, filename, size_bytes, created_at.
    Returns a PROPOSAL — nothing is written, and every group carries the reason
    it was grouped so a reviewer can disagree with it.
    """
    # 1. Collapse duplicates. Done FIRST: grouping revisions before this reports
    #    two revisions of a drawing that was merely uploaded twice.
    seen: dict[tuple, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    # Sorted by tidiness first, so the survivor is chosen rather than whichever
    # the database happened to return first.
    for d in sorted(documents, key=_tidiness):
        k = _dup_key(d)
        if k in seen:
            duplicates.append({"id": d["id"], "filename": d["filename"],
                               "duplicate_of": seen[k]["id"],
                               "duplicate_of_filename": seen[k]["filename"],
                               "reason": "same name and same byte size"})
        else:
            seen[k] = d

    # 1b. Collapse DWG/DXF pairs of one drawing. They survive the duplicate
    #     check because the two formats have different byte sizes, and without
    #     this they surface as a revision history that does not exist.
    by_key: dict[str, list[dict[str, Any]]] = {}
    for d in seen.values():
        by_key.setdefault(sheet_key(d["filename"]), []).append(d)
    kept: list[dict[str, Any]] = []
    for _k, group in by_key.items():
        # Only a DIFFERENCE of format collapses. Two .dwg files of one sheet are
        # revisions of it — an earlier version of this check looked at "is CAD"
        # rather than "which CAD", and quietly swallowed a genuine reissue.
        dxf = [d for d in group if _strip_ext(d["filename"])[1] == ".dxf"]
        dwg = [d for d in group if _strip_ext(d["filename"])[1] == ".dwg"]
        if dxf and dwg:
            winner = dxf[0]
            for loser in dwg:
                duplicates.append({
                    "id": loser["id"], "filename": loser["filename"],
                    "duplicate_of": winner["id"],
                    "duplicate_of_filename": winner["filename"],
                    "reason": "same drawing exported in another CAD format",
                })
            group = [d for d in group if d not in dwg]
        kept.extend(group)

    # 2. Group what remains by sheet identity.
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in kept:
        groups.setdefault(sheet_key(d["filename"]), []).append(d)

    sheets: list[dict[str, Any]] = []
    for key, docs in groups.items():
        entries = []
        for d in docs:
            rev = extract_revision(d["filename"])
            dat = extract_issue_date(d["filename"])
            entries.append({
                "id": d["id"],
                "filename": d["filename"],
                "size_bytes": d.get("size_bytes"),
                "revision": rev["label"] if rev else None,
                "issued": ("%04d-%02d-%02d" % dat["date"]) if dat else None,
                # Ordering: an explicit date beats a revision label beats upload
                # order. A drawing issued in November supersedes one issued in
                # October whatever the labels say.
                "_sort": (
                    dat["date"] if dat else (0, 0, 0),
                    rev["sort"] if rev else (1, 0),
                    str(d.get("created_at") or ""),
                ),
            })
        entries.sort(key=lambda e: e["_sort"], reverse=True)

        why = ("date_token" if any(e["issued"] for e in entries) else
               "revision_token" if any(e["revision"] for e in entries) else
               "name_only")
        for i, e in enumerate(entries):
            e["is_current"] = i == 0
            e.pop("_sort")

        # Reported, not used for grouping. Taken from the current revision.
        sheet_no = extract_sheet_number(entries[0]["filename"])
        sheets.append({
            "key": key,
            "sheet_number": sheet_no,
            "label": entries[0]["filename"],
            "revisions": entries,
            "revision_count": len(entries),
            # A single file is not a revision history. Said explicitly so the UI
            # does not present "1 revision" as though it had established one.
            "has_history": len(entries) > 1,
            "grouped_because": why,
            "grouped_because_plain": CONFIDENCE_REASON[why],
        })

    sheets.sort(key=lambda s: (not s["has_history"], s["label"].lower()))
    with_history = [s for s in sheets if s["has_history"]]
    return {
        "sheets": sheets,
        "duplicates": duplicates,
        "summary": {
            "documents_in": len(documents),
            "duplicates_found": len(duplicates),
            "distinct_drawings": len(sheets),
            "drawings_with_more_than_one_revision": len(with_history),
            "superseded_files": sum(s["revision_count"] - 1 for s in with_history),
        },
    }
