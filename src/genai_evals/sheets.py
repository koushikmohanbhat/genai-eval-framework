"""Review and label sheets as CSV or Excel (.xlsx), read back the same way.

An .xlsx sheet is for people who don't use a CLI: frozen header, wrapped text, a dropdown on the decision column,
and every column except the ones the reviewer fills in protected against accidental edits (no password: it's a
guard rail, not access control). It opens in Excel, Numbers, LibreOffice, and Google Sheets (File > Import), and
comes back in any of them as .xlsx or .csv. Needs the `xlsx` extra (openpyxl).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

WIDE = {"input": 60, "output": 60, "draft_answer": 50, "passage_excerpt": 60, "evidence": 40, "notes": 40,
        "corrected_input": 40, "corrected_answer": 40, "closest_passages": 30}


def _openpyxl():
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise SystemExit("Excel sheets need openpyxl: pip install 'genai-eval-framework[xlsx]' (or write .csv)") from e
    return openpyxl


def write_rows(path: str | Path, columns: list[str], rows: Iterable[dict[str, Any]],
               editable: list[str] | None = None, choices: dict[str, list[str]] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if path.suffix.lower() != ".xlsx":
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return path
    op = _openpyxl()
    from openpyxl.styles import Alignment, Font, PatternFill, Protection
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = op.Workbook()
    ws = wb.active
    ws.title = "review"
    ws.append(columns)
    for r in rows:
        ws.append([_cell(r.get(c)) for c in columns])
    editable = set(editable or [])
    fill = PatternFill("solid", fgColor="FFF4D6")
    for i, c in enumerate(columns, 1):
        letter = op.utils.get_column_letter(i)
        ws.column_dimensions[letter].width = WIDE.get(c, 14)
        head = ws.cell(row=1, column=i)
        head.font = Font(bold=True)
        for row in range(2, len(rows) + 2):
            cell = ws.cell(row=row, column=i)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if c in editable:
                cell.protection = Protection(locked=False)
                cell.fill = fill
        if c in (choices or {}) and rows:
            dv = DataValidation(type="list", formula1='"' + ",".join(choices[c]) + '"', allow_blank=True,
                                showErrorMessage=True, errorTitle="Pick from the list",
                                error="Choose one of: " + ", ".join(choices[c]))
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{len(rows) + 1}")
    ws.freeze_panes = "B2"
    ws.protection.sheet = True                       # guard the columns reviewers shouldn't change
    ws.protection.formatColumns = False
    ws.protection.formatRows = False
    wb.save(path)
    return path


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        import json
        return json.dumps(v, ensure_ascii=False)
    return v


def read_rows(path: str | Path) -> list[dict[str, str]]:
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    op = _openpyxl()
    wb = op.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    try:
        head = [str(h or "").strip() for h in next(it)]
    except StopIteration:
        return []
    out = []
    for vals in it:
        if vals is None or all(v in (None, "") for v in vals):
            continue
        out.append({h: ("" if v is None else str(v)) for h, v in zip(head, vals) if h})
    return out
