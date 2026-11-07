"""Offline stand-in for a document extraction model, so the pack runs with no keys or cloud access.

It extracts invoice fields with patterns (labels, emails, OCR-damaged text, Indian and European number formats,
lakh / crore), returns null for anything that is not an invoice, and emits JSON matching schema/output.schema.json.
Options (target `options:`) simulate typical regressions for experiments:
  model: "fast" for a cheaper model that sometimes drops the due date or picks the subtotal
  prose: share of answers wrapped in prose instead of bare JSON (a prompt that lost "return only JSON")

Replace it with your system: see README.md, "Connect your system".
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2}(?:st|nd|rd|th)? [A-Z][a-z]+ \d{4}|[A-Z][a-z]{2,8} \d{1,2}, \d{4})"
AMOUNT = r"((?:INR|Rs\.?|₹|USD|GBP|EUR)\s*[\d.,]+(?:\s*(?:lakh|crore))?(?:/-)?)"
GSTIN = r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b"
FIELDS = ["vendor_name", "invoice_number", "invoice_date", "due_date", "total_amount", "currency", "po_number", "tax_id"]


def _rnd(*parts) -> float:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _ocr_fix(t: str) -> str:
    t = re.sub(r"\b[lI1][nN][vV][oO0][iIl1][cC][eE]\b", lambda m: "Invoice" if m.group(0)[1].islower() else "INVOICE", t)
    t = re.sub(r"\bVend0r\b", "Vendor", t)
    return re.sub(r"\b0(?=[a-z]{2,})", "O", t)                      # 0akridge -> Oakridge


def _iso(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s.strip())
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return None


def _money(s: str) -> tuple[float | None, str | None]:
    cur = "INR" if re.search(r"INR|Rs|₹", s) else next((c for c in ("USD", "GBP", "EUR") if c in s), None)
    num = re.search(r"[\d][\d.,]*", s)
    if not num:
        return None, cur
    n = num.group(0).rstrip(".,")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", n):              # 2.350,00
        n = n.replace(".", "").replace(",", ".")
    else:
        n = n.replace(",", "")
    v = float(n)
    if re.search(r"lakh", s, re.I):
        v *= 1e5
    elif re.search(r"crore", s, re.I):
        v *= 1e7
    return round(v, 2), cur


def _first(patterns: list[str], text: str, flags=re.I) -> str | None:
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(1).strip(" ,.")
    return None


def fields(text: str, fast: bool = False, key: str = "") -> dict:
    t = _ocr_fix(text)
    if re.search(r"credit note|not an invoice", t, re.I):
        return dict.fromkeys(FIELDS)
    vendor = _first([r"(?:Vendor|Supplier)\s*:\s*([^\n(]+?)\s*(?:\(|\n|$)", r"^From:\s*([^@\n]+)$", r"Invoice from ([^\n]+)",
                     r"from ([A-Z][\w&. ]+?) dated", r"\n([A-Z][\w&. ]+?) invoice [A-Z]{1,4}-", r"Vendor ([A-Z][^,]+),"], t, re.I | re.M)
    number = _first([r"(?:Invoice (?:No|#|number|ref)|Inv No|invoice no\.)\s*[:#.]?\s*([A-Z]{1,6}[A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
                     r"\binv(?:oice)?\s+([A-Z]{1,6}-\d[\w/\-]*)"], t)
    inv_date = _first([rf"(?:Invoice Date|Issued|Date|dated|dt)\s*[:.]?\s*{DATE}"], t)
    due = _first([rf"(?:Due Date|Payment due|payable by|Due|due)\s*[:.]?\s*{DATE}"], t)
    totals = re.findall(rf"(?i)(?<![Ss]ub)(?<![Ss]ub )(?:Total Due|Grand Total|Amount payable|Amount due|Total|amt|for)\s*[:.]?\s*{AMOUNT}", t)
    subtotal = re.findall(rf"(?i)(?:Subtotal|Taxable value)\s*[:.]?\s*{AMOUNT}", t)
    raw = totals[-1] if totals else None
    if fast and subtotal and _rnd(key, "subtotal") < 0.3:
        raw = subtotal[0]
    amount, cur = _money(raw) if raw else (None, None)
    po = _first([r"PO Number\s*:\s*(PO-\d+)", r"Reference PO ([\w-]+)", r"\b(PO-\d{4,})\b"], t)
    tax = _first([GSTIN], t, 0)
    out = {"vendor_name": vendor, "invoice_number": number, "invoice_date": _iso(inv_date), "due_date": _iso(due),
           "total_amount": amount, "currency": cur, "po_number": po, "tax_id": tax}
    if fast and _rnd(key, "due") < 0.4:
        out["due_date"] = None
    return out


def extract(case, ctx) -> dict:
    opts = ctx.get("options") or {}
    fast = opts.get("model") == "fast"
    key = f"{case.id}:{ctx.get('repeat', 0)}"
    d = fields(case.input_text(), fast, key)
    out = json.dumps(d)
    if _rnd(key, "prose") < float(opts.get("prose", 0)):
        out = "Here are the extracted fields:\n" + out
    lat, cost = 1300 + int(_rnd(key, "lat") * 900), 0.0042
    if fast:
        lat, cost = lat * 0.45, cost * 0.3
    return {"output": out, "output_json": d if out.startswith("{") else None, "latency_ms": round(lat), "cost_usd": round(cost, 6)}
