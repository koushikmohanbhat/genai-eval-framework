"""Personal-identifier detectors, grouped into sets a suite or a domain overlay switches on.

The core ships no domain assumptions beyond the defaults (`global`, `in`, `pci`, which is what earlier versions
detected). A suite picks sets and adds its own:

    pii:
      sets: [global, pci, us_hipaa]
      custom:
        - {name: POLICY_NO, pattern: "\\bMOT-\\d{4}-\\d{6}\\b"}
        - {name: MEMBER_ID, pattern: "\\b[A-Z]{3}\\d{9}\\b", validator: luhn, context: "member|subscriber"}

Overlays add sets and custom detectors the same way (`pii:` in overlay.yaml). The same detectors drive the
tokeniser (what judges and stored results see) and the `pii_leak` grader. They find structured identifiers only:
names, addresses and free-text health details need an NER-based detector or upstream redaction.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Callable

# ---- checksums -------------------------------------------------------------------------------------------------------

_VERHOEFF_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
               [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
               [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
               [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
               [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
               [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(number: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[c])


def luhn_valid(number: str) -> bool:
    if not number.isdigit():
        return False
    total, alt = 0, False
    for ch in reversed(number):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def npi_valid(number: str) -> bool:
    """US National Provider Identifier: Luhn over the 80840 prefix and the 10 digits."""
    d = _digits(number)
    return len(d) == 10 and luhn_valid("80840" + d)


def nhs_valid(number: str) -> bool:
    """UK NHS number: modulus 11 over the first nine digits."""
    d = _digits(number)
    if len(d) != 10:
        return False
    check = 11 - sum(int(x) * w for x, w in zip(d[:9], range(10, 1, -1))) % 11
    check = 0 if check == 11 else check
    return check != 10 and check == int(d[9])


def iban_valid(value: str) -> bool:
    """ISO 13616 IBAN: move the first four characters to the end, letters to numbers, mod 97 == 1."""
    s = re.sub(r"\s", "", value).upper()
    if not 15 <= len(s) <= 34:
        return False
    r = s[4:] + s[:4]
    try:
        return int("".join(str(int(ch, 36)) for ch in r)) % 97 == 1
    except ValueError:
        return False


def ipv4_valid(value: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_unspecified)


VALIDATORS: dict[str, Callable[[str], bool]] = {
    "verhoeff": lambda s: verhoeff_valid(_digits(s)),
    "luhn": lambda s: luhn_valid(_digits(s)),
    "card": lambda s: 13 <= len(_digits(s)) <= 19 and luhn_valid(_digits(s)),
    "npi": npi_valid,
    "nhs_mod11": nhs_valid,
    "iban_mod97": iban_valid,
    "ipv4": ipv4_valid,
}

# ---- detectors and sets ----------------------------------------------------------------------------------------------


@dataclass
class Detector:
    name: str
    pattern: re.Pattern[str]
    validator: str | None = None
    context: re.Pattern[str] | None = None     # a keyword that must appear shortly before the match
    priority: int = 50                         # lower wins when two detectors match overlapping text
    sets: tuple[str, ...] = field(default_factory=tuple)

    def valid(self, text: str, m: re.Match[str]) -> bool:
        if self.validator and not VALIDATORS[self.validator](m.group(0)):
            return False
        if self.context is not None and not self.context.search(text[max(0, m.start() - 40):m.start()]):
            return False
        return True


def _d(name, pattern, sets, validator=None, priority=50, context=None, flags=0) -> Detector:
    return Detector(name, re.compile(pattern, flags), validator, re.compile(context, re.I) if context else None,
                    priority, tuple(sets))


BUILTIN: list[Detector] = [
    # checksum-validated identifiers first: they win overlaps with pattern-only ones
    _d("AADHAAR", r"(?<!\d)[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?!\d)", ["in"], "verhoeff", 10),
    _d("CARD_NUMBER", r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)", ["pci", "banking"], "card", 11),
    _d("US_NPI", r"(?<!\d)[12]\d{9}(?!\d)", ["us_hipaa"], "npi", 12, context=r"\bNPI\b|provider (?:id|number)"),
    _d("UK_NHS", r"(?<!\d)\d{3}[ \-]?\d{3}[ \-]?\d{4}(?!\d)", ["uk"], "nhs_mod11", 13),
    _d("IBAN", r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b", ["banking", "eu"], "iban_mod97", 14),
    _d("US_SSN", r"(?<!\d)(?!000|666|9\d\d)\d{3}[\- ](?!00)\d{2}[\- ](?!0000)\d{4}(?!\d)", ["us_hipaa"], None, 20),
    _d("US_MBI", r"\b[1-9][AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9]\d-?[AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9]\d-?"
                 r"[AC-HJKMNP-RT-Y]{2}\d{2}\b", ["us_hipaa"], None, 21),
    _d("EMAIL", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+\b", ["global"], None, 30),
    _d("UPI_ID", r"\b[A-Za-z0-9.\-_]{2,256}@(?:ok)?[a-zA-Z]{2,64}\b(?!\.)", ["in"], None, 35),
    _d("PAN", r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b", ["in"], None, 40),
    _d("UK_NINO", r"\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b",
       ["uk"], None, 41),
    _d("PHONE_IN", r"(?<![\d+])(?:\+91[\-\s]?|0)?[6-9]\d{4}[\-\s]?\d{5}(?!\d)", ["in"], None, 60),
    _d("US_PHONE", r"(?<![\d+])(?:\+1[\s.\-]?)?\(?[2-9]\d{2}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)", ["us_hipaa", "us"], None, 61),
    _d("IP_ADDRESS", r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", ["us_hipaa"], "ipv4", 70),
]
SETS: dict[str, list[str]] = {}
for _det in BUILTIN:
    for _s in _det.sets:
        SETS.setdefault(_s, []).append(_det.name)
SETS["us"] = sorted(set(SETS.get("us", [])) | {"US_SSN"})
DEFAULT_SETS = ["global", "in", "pci"]
SET_DESCRIPTIONS = {
    "global": "email addresses",
    "in": "India: Aadhaar (Verhoeff), PAN, UPI ids, +91 phones",
    "pci": "payment cards (Luhn), PCI DSS primary account numbers",
    "banking": "IBAN (mod 97) and payment cards",
    "eu": "IBAN (mod 97)",
    "us": "US: SSN, phone numbers",
    "us_hipaa": "US HIPAA Safe Harbor identifiers with a structure: SSN, Medicare MBI, NPI (Luhn, after 'NPI'), "
                "phone numbers, IPv4 addresses",
    "uk": "UK: NHS number (mod 11), National Insurance number",
}


def build(cfg: dict[str, Any] | None = None) -> list[Detector]:
    """Detectors for a `pii:` config ({sets, custom}), ordered by priority."""
    cfg = cfg or {}
    sets = cfg.get("sets") or DEFAULT_SETS
    unknown = [s for s in sets if s not in SETS]
    if unknown:
        raise ValueError(f"pii: unknown detector set(s) {unknown} (known: {', '.join(sorted(SETS))})")
    names = {n for s in sets for n in SETS[s]}
    dets = [d for d in BUILTIN if d.name in names]
    for c in cfg.get("custom") or []:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", c.get("name", "")):
            raise ValueError(f"pii.custom: name must be UPPER_SNAKE_CASE, got {c.get('name')!r}")
        if c.get("validator") and c["validator"] not in VALIDATORS:
            raise ValueError(f"pii.custom {c['name']}: unknown validator {c['validator']!r} (known: {', '.join(VALIDATORS)})")
        dets.append(_d(c["name"], c["pattern"], ["custom"], c.get("validator"), int(c.get("priority", 45)),
                       c.get("context"), re.I if c.get("ignore_case") else 0))
    return sorted(dets, key=lambda d: d.priority)


_ACTIVE: list[Detector] = build()


def configure(cfg: dict[str, Any] | None) -> list[Detector]:
    """Set the detectors used by the tokeniser and pii_leak for this run (the Runner calls this with suite.pii)."""
    global _ACTIVE
    _ACTIVE = build(cfg)
    return _ACTIVE


def active_names() -> list[str]:
    return [d.name for d in _ACTIVE]


@dataclass
class Finding:
    type: str
    start: int
    end: int
    text: str


def detect(text: str, types: set[str] | None = None, detectors: list[Detector] | None = None) -> list[Finding]:
    out: list[Finding] = []
    taken: list[tuple[int, int]] = []
    for det in detectors if detectors is not None else _ACTIVE:
        if types and det.name not in types:
            continue
        for m in det.pattern.finditer(text or ""):
            if not det.valid(text, m):
                continue
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            out.append(Finding(det.name, m.start(), m.end(), m.group(0)))
    return sorted(out, key=lambda f: f.start)
