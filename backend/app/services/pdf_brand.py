"""Shared PDF branding — the BRIO logo as an inline data URL.

The logo (`app/assets/logo.png`, from builtbybrio.com) is embedded as a base64
data URL so the WeasyPrint renderers can place it with a plain <img> tag and
no network fetch.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("brio.pdf_brand")

_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logo.png"


@lru_cache(maxsize=1)
def logo_data_url() -> str:
    """The BRIO logo as a base64 PNG data URL. Empty string if the asset is
    missing (the PDF still renders, just without the logo)."""
    try:
        data = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{data}"
    except OSError as exc:  # noqa: BLE001
        logger.warning("logo asset unavailable: %s", exc)
        return ""
