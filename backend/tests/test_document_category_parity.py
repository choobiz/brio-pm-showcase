"""Every category the Documents picker offers must be one the API accepts.

The two lists have drifted twice: six categories were refused with a 422 until
2026-10-12, and "prepared" until 2026-10-14. This reads the picker's list
straight from the frontend source so the next drift fails here, not in use.
"""

import re
from pathlib import Path
from typing import get_args

from app.schemas.document import DocumentCategory

PICKER = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "documents.ts"


def _picker_values() -> list[str]:
    src = PICKER.read_text()
    block = re.search(r"DOCUMENT_CATEGORIES[^=]*=\s*\[(.*?)\n\];", src, re.S)
    assert block, "DOCUMENT_CATEGORIES not found in documents.ts"
    values = re.findall(r'value:\s*"([a-z_]+)"', block.group(1))
    assert len(values) >= 10, f"parsed only {values}"
    return values


def test_every_picker_category_is_accepted_by_the_api():
    accepted = set(get_args(DocumentCategory))
    missing = [v for v in _picker_values() if v not in accepted]
    assert missing == [], f"picker offers categories the API refuses: {missing}"
