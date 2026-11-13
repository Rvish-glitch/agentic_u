"""Unit tests for normalisation and parsing utilities."""

from datetime import datetime
from solution.config import IST
from solution.crypto import decrypt_pii, encrypt_pii, get_phone_hash, mask_phone
from solution.normalizers import (
    format_ist_iso,
    normalise_header,
    parse_lead_date,
    parse_lead_decimal,
    parse_lead_enum,
    parse_phone,
    parse_timestamp,
)


def test_normalise_header():
    assert normalise_header("customer_name") == "customer name"
    assert normalise_header("  Insured___Name  ") == "insured name"
    assert normalise_header("Mobile No") == "mobile no"
    assert normalise_header("Order   ID") == "order id"
    assert normalise_header("agent_notes") == "agent notes"


def test_parse_phone_valid():
    # 10 digits starting with 6, 7, 8, 9
    assert parse_phone("9876543210") == "+919876543210"
    assert parse_phone("8123456789") == "+918123456789"
    assert parse_phone("7123456789") == "+917123456789"
    assert parse_phone("6123456789") == "+916123456789"

    # Formatted variations
    assert parse_phone("+91 98765 43210") == "+919876543210"
    assert parse_phone("09876543210") == "+919876543210"
    assert parse_phone("91-98765-43210") == "+919876543210"
    assert parse_phone("(+91) 9876543210") == "+919876543210"
    assert parse_phone("+919876543210") == "+919876543210"


def test_parse_phone_invalid():
    # Bad lengths
    assert parse_phone("987654321") is None  # 9 digits
    assert parse_phone("9876543210123") is None  # >12 digits
    # Bad prefixes
    assert parse_phone("5876543210") is None  # starts with 5
    assert parse_phone("022-2500-1234") is None  # landline
    assert parse_phone("+1 415 555 0134") is None  # US phone
    # Non-digits
    assert parse_phone("N/A") is None
    assert parse_phone("") is None
    assert parse_phone("   ") is None


def test_parse_lead_date_valid():
    # YYYY-MM-DD
    assert parse_lead_date("2026-11-23") == "2026-11-23"
    # DD/MM/YYYY (day-first)
    assert parse_lead_date("27/10/2026") == "2026-10-27"
    assert parse_lead_date("05/11/2026") == "2026-11-05"
    # DD-MM-YYYY
    assert parse_lead_date("21-11-2026") == "2026-11-21"
    # DD-Mon-YYYY (case-insensitive)
    assert parse_lead_date("01-Dec-2026") == "2026-12-01"
    assert parse_lead_date("29-dec-2026") == "2026-12-29"
    assert parse_lead_date("15-AUG-2026") == "2026-08-15"


def test_parse_lead_date_invalid():
    assert parse_lead_date("31/02/2026") is None  # February 31st impossible
    assert parse_lead_date("2026-13-01") is None  # Month 13 impossible
    assert parse_lead_date("soon") is None
    assert parse_lead_date("TBD") is None
    assert parse_lead_date("") is None


def test_parse_lead_decimal_valid():
    assert parse_lead_decimal("6850") == 6850.0
    assert parse_lead_decimal("29678.29") == 29678.29
    assert parse_lead_decimal("₹27,554") == 27554.0
    assert parse_lead_decimal("Rs. 31629") == 31629.0
    assert parse_lead_decimal("Rs 15,000") == 15000.0
    assert parse_lead_decimal("INR 25301.4") == 25301.4
    assert parse_lead_decimal("INR 12,345.678") == 12345.68  # rounded to 2 places


def test_parse_lead_decimal_invalid():
    assert parse_lead_decimal("N/A") is None
    assert parse_lead_decimal("-") is None
    assert parse_lead_decimal("twelve thousand") is None
    assert parse_lead_decimal("-500") is None  # must be >= 0
    assert parse_lead_decimal("") is None


def test_parse_lead_enum():
    allowed = ["en", "hi", "ta", "kn", "te", "mr"]
    assert parse_lead_enum("en", allowed) == "en"
    assert parse_lead_enum("HI", allowed) == "hi"
    assert parse_lead_enum(" ta ", allowed) == "ta"
    assert parse_lead_enum("Hindi", allowed) is None
    assert parse_lead_enum("english", allowed) is None
    assert parse_lead_enum("", allowed) is None


def test_parse_timestamp():
    # UTC
    dt_utc = parse_timestamp("2026-10-01T05:26:10Z")
    assert format_ist_iso(dt_utc) == "2026-10-01T10:56:10+05:30"

    # Offset
    dt_off = parse_timestamp("2026-10-01T10:40:19+05:30")
    assert format_ist_iso(dt_off) == "2026-10-01T10:40:19+05:30"

    # Naive (means IST per spec)
    dt_naive = parse_timestamp("2026-10-01 09:15:55")
    assert format_ist_iso(dt_naive) == "2026-10-01T09:15:55+05:30"


def test_crypto_pii():
    phone = "+919876543210"
    masked = mask_phone(phone)
    assert masked == "+91******3210"

    h1 = get_phone_hash(phone)
    h2 = get_phone_hash(phone)
    assert h1 == h2  # deterministic
    assert len(h1) == 64

    # Different phones produce different hashes
    assert get_phone_hash("+919876543211") != h1

    # AES encryption/decryption
    name = "Aarav Sharma"
    encrypted = encrypt_pii(name)
    assert encrypted != name
    decrypted = decrypt_pii(encrypted)
    assert decrypted == name

