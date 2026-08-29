# Lightweight Indic language and script detection based on Unicode block ranges
# plus high-frequency word sets. No external model dependency.

UNICODE_BLOCKS: dict[str, set[str]] = {
    "devanagari": {(0x0900, 0x097F), (0xA8E0, 0xA8FF)},
    "tamil": {(0x0B80, 0x0BFF)},
    "telugu": {(0x0C00, 0x0C7F)},
    "kannada": {(0x0C80, 0x0CFF)},
    "gujarati": {(0x0A80, 0x0AFF)},
    "bengali": {(0x0980, 0x09FF)},
    "punjabi": {(0x0A00, 0x0A7F)},
    "odia": {(0x0B00, 0x0B7F)},
    "malayalam": {(0x0D00, 0x0D7F)},
}

SCRIPT_TO_LANGUAGE = {
    "devanagari": "hi",
    "tamil": "ta",
    "telugu": "te",
    "kannada": "kn",
    "gujarati": "gu",
    "bengali": "bn",
    "punjabi": "pa",
    "odia": "or",
    "malayalam": "ml",
}

MARATHI_WORDS = {"काय", "आहे", "सांगा", "मला", "आपण", "व", "करा", "मध्ये", "नाही", "होते"}
HINDI_WORDS = {"मौसम", "कैसा", "क्या", "है", "हाँ", "नहीं", "और", "में", "कर", "की"}


def detect_script(text: str) -> str | None:
    counts: dict[str, int] = {}
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        for script, ranges in UNICODE_BLOCKS.items():
            for start, end in ranges:
                if start <= code <= end:
                    counts[script] = counts.get(script, 0) + 1
                    break
    if not counts:
        return None
    return max(counts, key=counts.get)


def detect_language(text: str) -> str:
    """Return an ISO-639-1 language code, 'en' for Latin text, or 'unknown'."""
    script = detect_script(text)
    if script is None:
        return "en" if any(c.isalpha() for c in text) else "unknown"
    if script == "devanagari":
        marathi_score = sum(text.count(w) for w in MARATHI_WORDS)
        hindi_score = sum(text.count(w) for w in HINDI_WORDS)
        return "mr" if marathi_score > hindi_score else "hi"
    return SCRIPT_TO_LANGUAGE.get(script, script)


INDIC_LANGUAGES = {"hi", "ta", "te", "kn", "gu", "bn", "pa", "or", "ml", "mr"}


def is_indic(text: str) -> bool:
    return detect_language(text) in INDIC_LANGUAGES


def language_label(code: str) -> str:
    return {
        "hi": "Hindi",
        "mr": "Marathi",
        "ta": "Tamil",
        "te": "Telugu",
        "kn": "Kannada",
        "gu": "Gujarati",
        "bn": "Bengali",
        "pa": "Punjabi",
        "or": "Odia",
        "ml": "Malayalam",
        "en": "English",
        "unknown": "Unknown",
    }.get(code, code)