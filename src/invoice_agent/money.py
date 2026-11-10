"""Number, IBAN and identifier helpers. Pure functions, no I/O."""

from __future__ import annotations

import re
import string

CENT = 0.005


def close(a: float, b: float, tol: float = 0.011) -> bool:
    return abs(round(a, 2) - round(b, 2)) <= tol


def amount_variants(value: float) -> set[str]:
    """Ways an amount can be printed: 1234.5 -> {'1,234.50', '1.234,50', '1 234,50', ...}."""
    v = abs(value)
    whole, frac = f"{v:.2f}".split(".")
    groups = f"{int(whole):,}"
    dots = groups.replace(",", ".")
    spaces = groups.replace(",", " ")
    out = {
        f"{whole}.{frac}",
        f"{whole},{frac}",
        f"{groups}.{frac}",
        f"{dots},{frac}",
        f"{spaces},{frac}",
        f"{spaces}.{frac}",
    }
    return out


def squash(text: str) -> str:
    """Lowercase and drop whitespace, so 'GB29 NWBK 6016' matches 'gb29nwbk6016'."""
    return re.sub(r"\s+", "", text).lower()


def normalize_id(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def iban_is_valid(iban: str | None) -> bool:
    """ISO 13616 mod-97 check."""
    s = normalize_id(iban)
    if len(s) < 15 or len(s) > 34 or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def iban_with_check_digits(country: str, bban: str) -> str:
    """Build a valid IBAN from a country code and BBAN (used by the synthetic generator)."""
    rearranged = bban + country + "00"
    digits = "".join(str(int(ch, 36)) for ch in rearranged.upper())
    check = 98 - int(digits) % 97
    return f"{country}{check:02d}{bban}"


def format_iban(iban: str) -> str:
    s = normalize_id(iban)
    return " ".join(s[i : i + 4] for i in range(0, len(s), 4))


ALNUM = string.ascii_uppercase + string.digits
