"""Data normalisation and parsing utilities adhering strictly to the assignment specifications."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from solution.config import IST

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def normalise_header(header: str) -> str:
    """Normalise a CSV column header:
    Lower-case it, trim it, and replace every run of '_' or whitespace with a single space.
    """
    if not header:
        return ""
    h = header.strip().lower()
    return re.sub(r"[_\s]+", " ", h)


def parse_phone(val: str) -> str | None:
    """Parse and validate an Indian mobile number.
    
    Rules:
    - Strip every non-digit.
    - If 12 digits starting with '91', drop the '91'.
    - If 11 digits starting with '0', drop the '0'.
    - Valid only if exactly 10 digits and starts with 6, 7, 8 or 9.
    - Canonical storage format: '+91XXXXXXXXXX'.
    """
    if not val:
        return None
    val = val.strip()
    if not val:
        return None
    
    digits = re.sub(r"\D", "", val)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None


def parse_lead_date(val: str) -> str | None:
    """Parse and validate a date string from a lead file.
    
    Accepted formats (day-first):
    - YYYY-MM-DD
    - DD/MM/YYYY
    - DD-MM-YYYY
    - DD-Mon-YYYY (e.g. 01-Dec-2026; month is case-insensitive)
    
    Impossible dates (like 31/02) are invalid.
    Returns canonical 'YYYY-MM-DD' string, or None if invalid.
    """
    if not val:
        return None
    val = val.strip()
    if not val:
        return None

    # 1. YYYY-MM-DD
    m1 = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", val)
    if m1:
        y, m, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        try:
            dt = datetime(y, m, d)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 2. DD/MM/YYYY
    m2 = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", val)
    if m2:
        d, m, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            dt = datetime(y, m, d)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 3. DD-MM-YYYY
    m3 = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", val)
    if m3:
        d, m, y = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        try:
            dt = datetime(y, m, d)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 4. DD-Mon-YYYY (e.g. 21-Nov-2026, 01-dec-2026)
    m4 = re.match(r"^(\d{1,2})-([a-zA-Z]{3})-(\d{4})$", val)
    if m4:
        d, mon_str, y = int(m4.group(1)), m4.group(2).lower(), int(m4.group(3))
        if mon_str not in MONTHS:
            return None
        m = MONTHS[mon_str]
        try:
            dt = datetime(y, m, d)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def parse_lead_decimal(val: str) -> float | None:
    """Parse and validate a decimal amount.
    
    Rules:
    - Optionally prefixed by ₹, Rs, Rs. or INR (case-insensitive, optional space).
    - Commas are thousands separators.
    - Must be >= 0.
    - Round to 2 places.
    """
    if not val:
        return None
    val = val.strip()
    if not val:
        return None

    pattern = r"^(?:₹|rs\.|rs|inr)?\s*([0-9,]+(?:\.[0-9]+)?)$"
    m = re.match(pattern, val, re.IGNORECASE)
    if not m:
        return None
    
    num_str = m.group(1).replace(",", "")
    try:
        d = Decimal(num_str)
        if d < 0:
            return None
        return round(float(d), 2)
    except (InvalidOperation, ValueError):
        return None


def parse_lead_enum(val: str, allowed_values: list[str]) -> str | None:
    """Parse an enum value.
    
    Compared case-insensitively against allowed values and stored lower-case.
    """
    if not val:
        return None
    val = val.strip().lower()
    if not val:
        return None
    
    allowed_map = {v.lower(): v.lower() for v in allowed_values}
    return allowed_map.get(val, None)


def parse_timestamp(ts_str: str) -> datetime:
    """Parse an event timestamp into an aware datetime in IST timezone.
    
    Three forms:
    - UTC ('...Z')
    - offset ('...+05:30' or other ISO offset)
    - naive ('2026-10-03 10:01:00') -> treated as IST per spec
    """
    ts_str = ts_str.strip()
    if ts_str.endswith("Z"):
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    elif "T" in ts_str:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
    else:
        # Naive format e.g. '2026-10-03 10:01:00' -> IST per spec
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    
    return dt.astimezone(IST)


def format_ist_iso(dt: datetime) -> str:
    """Format an aware datetime as ISO-8601 in IST (e.g. 2026-10-14T17:55:24+05:30)."""
    ist_dt = dt.astimezone(IST)
    # Ensure seconds with no fractional part if not present
    return ist_dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")

