"""Strict financial amount parsing, shared by contracts and source validation."""
from decimal import Decimal
import re
import unicodedata

_AMOUNT = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?")
_SOURCE_AMOUNT = re.compile(r"(?<![0-9.,+\-])[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![0-9.,])")


def parse_amount_text(text: str) -> Decimal:
    """Return a finite Decimal from an exact amount token, without unit guessing.

    Input is the raw cell text, optionally grouped by commas or full-width glyphs.
    Missing markers, embedded units and malformed grouping are rejected. No date
    or period inference is performed; callers bind the token to source context.
    """
    normalized = unicodedata.normalize("NFKC", text).strip().replace("−", "-")
    if not _AMOUNT.fullmatch(normalized):
        raise ValueError("原始金额文本格式不明确")
    return Decimal(normalized.replace(",", ""))


def amount_occurs_in_source(amount: str, source: str) -> bool:
    """Require a complete source token, preserving its sign and decimal places."""
    normalize = lambda s: unicodedata.normalize("NFKC", s).replace("−", "-").replace(",", "").lstrip("+")
    wanted = normalize(amount.strip())
    return wanted in {normalize(m.group(0)) for m in _SOURCE_AMOUNT.finditer(unicodedata.normalize("NFKC", source).replace("−", "-"))}
