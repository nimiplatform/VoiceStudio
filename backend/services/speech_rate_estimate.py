_RATE_CPS = {
    "en": 15.0, "de": 14.0, "fr": 15.0, "es": 15.5, "it": 15.0, "pt": 15.0,
    # CJK — logographic / mora-based scripts, fewer chars per second.
    "ja": 10.0, "ko": 10.0, "zh": 6.0,
    # Indic — Devanagari/Bengali/Tamil/Telugu/etc. compound graphemes
    # decompose into multiple codepoints; spoken syllable rate is
    # closer to English but the codepoint count is higher.
    "hi": 17.0, "bn": 17.0, "ta": 14.0, "te": 14.0, "mr": 16.0,
    "gu": 16.0, "kn": 14.0, "ml": 14.0, "pa": 16.0, "or": 16.0,
    "ur": 13.0,
    # RTL / Semitic — Arabic & Hebrew have shorter codepoint counts per
    # word than English (no vowel chars written) so cps reads lower.
    "ar": 12.0, "he": 12.0, "fa": 13.0,
    # Southeast Asian — Thai is contiguous (no spaces); Vietnamese is
    # concise; Indonesian is concise but Latin-scripted.
    "th": 10.0, "vi": 16.0, "id": 14.0, "ms": 14.0,
    # Slavic + Turkic — agglutinative or compound-heavy; long words.
    "ru": 13.0, "pl": 13.0, "uk": 13.0, "cs": 13.0, "tr": 12.0,
    # Nordic / Greek — close to mainland European baseline.
    "el": 14.0, "nl": 14.0, "sv": 14.0, "no": 14.0, "da": 14.0, "fi": 13.0,
}

# Tolerance window — if predicted ratio is within this of 1.0 we accept.
TOL_LOW = 0.92
TOL_HIGH = 1.08

# Max LLM attempts per segment. Past this we just return the best we got.
MAX_ATTEMPTS = 3

# Acceptance window for real TTS measurements. One rewrite is made between
# renders; repeating guesses inside a call would discard the useful evidence.
MEASURED_TOL_LOW = 0.9
MEASURED_TOL_HIGH = 1.04


def expected_duration(text: str, lang: str = "en") -> float:
    """Rough CPS-based duration estimate. Returns seconds."""
    cps = _RATE_CPS.get(lang.split("-")[0].lower(), 13.0)
    return len(text) / max(1.0, cps)


def rate_ratio(text: str, slot_seconds: float, lang: str = "en") -> float:
    """How far off the text is from the slot. 1.0 = perfect."""
    if slot_seconds <= 0:
        return 1.0
    return expected_duration(text, lang) / slot_seconds
