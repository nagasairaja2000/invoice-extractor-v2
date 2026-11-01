"""File-system helpers that tolerate transient Windows file locks."""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def remove_file(path: str | Path, *, attempts: int = 10, delay: float = 0.1) -> bool:
    """Delete ``path`` if it exists, retrying briefly on ``PermissionError``.

    On Windows an antivirus scanner or an indexer can hold a newly written file
    open for a few milliseconds. Retrying avoids turning harmless cleanup into
    a 500 response. Returns ``True`` when the file is gone (or never existed).
    """
    target = Path(path)
    for attempt in range(attempts):
        try:
            target.unlink(missing_ok=True)
            return True
        except PermissionError:
            if attempt < attempts - 1:
                time.sleep(delay)
    logger.warning("Could not delete %s; it is still locked by another process.", target)
    return False
