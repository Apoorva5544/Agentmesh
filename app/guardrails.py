import re
from typing import TypedDict


class PIIDetection(TypedDict):
    type: str
    count: int


PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "phone": re.compile(r"\b(?:\+?91[\s-]?)?[6-9]\d{9}\b"),
    "email": re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}


def redact_pii(text: str) -> tuple[str, list[PIIDetection]]:
    """Scan for Indian PII and replace matches with redaction markers.

    Returns (redacted_text, detections) where each detection carries the entity
    type and match count. Used before sending prompts to hosted providers.
    """
    detections: list[PIIDetection] = []
    for pii_type, pattern in PII_PATTERNS.items():
        matches = pattern.findall(text)
        if not matches:
            continue
        detections.append({"type": pii_type, "count": len(matches)})
        text = pattern.sub(f"[REDACTED_{pii_type.upper()}]", text)
    return text, detections


def redaction_markers(text: str) -> int:
    """Count redaction markers present in a string (for ledger/dashboard use)."""
    return len(re.findall(r"\[REDACTED_[A-Z_]+\]", text))