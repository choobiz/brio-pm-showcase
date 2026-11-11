"""Rendering a project note to a PDF a supplier can be sent.

The cladding takeoff and the window schedule go out for pricing, and a supplier
is never given a login. The note has to become a file.
"""

from __future__ import annotations

import base64

import pytest

from app.services.narrative_pdf import resolve_images, _markdown_to_html


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")


class TestImageResolution:
    """⚠️ Images are stored as `doc:<uuid>`, never as URLs — an R2 presign dies
    in minutes and a PDF outlives it by years. So the bytes must be INLINED."""

    def test_a_reference_becomes_an_inlined_data_uri(self):
        ref = "0199e1f6-1a2b-4c3d-8e9f-aabbccddeeff"
        out = resolve_images(f"![plan](doc:{ref})", {ref: PNG})
        assert "data:image/png;base64," in out
        assert "doc:" not in out

    def test_resolution_is_case_insensitive_on_the_uuid(self):
        ref = "0199E1F6-1A2B-4C3D-8E9F-AABBCCDDEEFF"
        out = resolve_images(f"![plan](doc:{ref})", {ref.lower(): PNG})
        assert "data:image/png;base64," in out

    def test_a_missing_asset_reads_as_missing_not_as_a_broken_image(self):
        out = resolve_images("![plan](doc:0199e1f6-1a2b-4c3d-8e9f-aabbccddeeff)", {})
        assert "doc:MISSING" in out
        assert "data:image" not in out

    def test_prose_without_references_is_untouched(self):
        md = "Perimeter is 130.4 lf at 10'-6\"."
        assert resolve_images(md, {}) == md


class TestMarkdownFeaturesTheNotesActuallyUse:
    def test_gfm_tables_survive(self):
        """Every takeoff in these notes is a table; losing them loses the note."""
        html = _markdown_to_html("| a | b |\n| --- | --- |\n| 1 | 2 |")
        assert "<table>" in html and "<td>1</td>" in html

    def test_headings_and_emphasis_survive(self):
        html = _markdown_to_html("## Cladding\n\n**130.4 lf** of perimeter")
        assert "<h2>" in html and "<strong>130.4 lf</strong>" in html

    def test_a_single_newline_breaks_the_line(self):
        """Notes are written with hard wraps; without nl2br they reflow into mush."""
        assert "<br" in _markdown_to_html("line one\nline two")


def _weasyprint_available() -> bool:
    """WeasyPrint needs native pango/cairo, which a dev Mac usually lacks but the
    API container has. Importing it is not enough — loading the shared library is
    what fails, with OSError at call time."""
    try:
        from weasyprint import HTML

        HTML(string="<p>x</p>").write_pdf()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _weasyprint_available(),
                    reason="weasyprint native libraries not available here")
class TestEndToEnd:
    def test_it_produces_a_real_pdf(self):
        from app.services.narrative_pdf import render_narrative_pdf

        pdf = render_narrative_pdf(
            {"title": "Cladding takeoff", "version_number": 3,
             "created_at": "2026-10-02T00:00:00Z",
             "content_md": "## Quantities\n\n| item | qty |\n| --- | --- |\n"
                           "| perimeter | 130.4 lf |\n"},
            {"name": "1234 Example Way"}, {})
        assert pdf[:5] == b"%PDF-", "must be a PDF, not an HTML error page"
        assert len(pdf) > 1000
