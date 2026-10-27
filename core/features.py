"""Build-time application capabilities shared by UI and domain code."""
from __future__ import annotations

import sys


def metadata_writing_enabled() -> bool:
    """Return whether this build permits writing descriptions into media files."""
    return not bool(getattr(sys, '_seename_metadata_write_disabled', False))
