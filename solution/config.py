"""Configuration settings, constants, and paths for the pipeline."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# IST timezone: UTC + 5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Default paths
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = os.environ.get("PIPELINE_DB_PATH", str(BASE_DIR / "pipeline.db"))
DEFAULT_SCHEMA_DIR = os.environ.get("PIPELINE_SCHEMA_DIR", str(BASE_DIR / "assignment-a-data" / "schemas"))
DEFAULT_REPORTS_DIR = os.environ.get("PIPELINE_REPORTS_DIR", str(BASE_DIR / "reports"))

# Secret key for PII protection (HMAC hashing & AES encryption).
# Can be supplied via environment variable PII_SECRET_KEY.
# A stable default 32-byte key is provided for reproducible local evaluation.
DEFAULT_SECRET_KEY = os.environ.get(
    "PIPELINE_SECRET_KEY",
    "k4N8fQ7Z9xL2pW5vY8mR1tE4uI7oA0sD3fG6hJ9lK2="
)

