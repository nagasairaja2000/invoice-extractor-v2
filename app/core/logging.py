"""Centralised logging configuration."""

from __future__ import annotations

import logging
import sys

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    # Third-party loggers are noisy at INFO level.
    for noisy in ("httpx", "httpcore", "google_genai", "google.api_core", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
