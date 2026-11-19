"""`genai-evals label <sheet>`: a local page for subject-matter experts to work through a label or review sheet.

One item at a time, big buttons, notes, keyboard shortcuts; every decision is saved straight back into the same
sheet (.csv or .xlsx), so the usual commands (`calibrate --labels`, `labels agreement`, `cases promote --sheet`) read
it unchanged. It serves only on 127.0.0.1 with a one-time token in the URL, sends nothing anywhere, and shows only
what the sheet holds: label sheets never contain the judge's verdict, so the reviewer isn't anchored by it.
"""
from __future__ import annotations

import html
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .sheets import read_rows, write_rows

KINDS = {
    "label": {"choice": "label", "options": ["pass", "fail", "unsure"], "text": ["notes"],
              "show": ["tags", "input", "output"], "title": "Does the answer meet the bar?"},
    "review": {"choice": "decision", "options": ["keep", "fix", "drop"],
               "text": ["corrected_input", "corrected_answer", "corrected_answerable", "notes"],
               "show": ["kind", "flags", "tags", "input", "draft_answer", "answerable", "passage_excerpt", "evidence",
                        "source_location", "source_check"],
               "title": "Keep, fix or drop this draft case?"},
}


def kind_of(columns: list[str]) -> str:
    if "decision" in columns:
        return "review"
    if "label" in columns:
        return "label"
    raise SystemExit("not a label sheet (labels sheet / labels queue) or a review sheet (cases review-sheet)")


class SheetStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.rows = read_rows(self.path)
        if not self.rows:
            raise SystemExit(f"{self.path} has no rows")
        self.columns = list(self.rows[0].keys())
        self.kind = kind_of(self.columns)
        self.spec = KINDS[self.kind]
        self.lock = threading.Lock()

    def save(self, i: int, values: dict[str, str]) -> None:
        with self.lock:
            row = self.rows[i]
            for k, v in values.items():
                if k in ([self.spec["choice"], "reviewer"] + self.spec["text"]) and k in row:
                    row[k] = str(v)
            from .cases import REVIEW_EDITABLE
            from .labels import LABEL_EDITABLE
            editable = REVIEW_EDITABLE if self.kind == "review" else LABEL_EDITABLE
            tmp = self.path.with_name(self.path.stem + ".saving" + self.path.suffix)
            write_rows(tmp, self.columns, self.rows, editable=editable,
                       choices={self.spec["choice"]: self.spec["options"]})
            tmp.replace(self.path)

    def done(self) -> int:
        return sum(1 for r in self.rows if (r.get(self.spec["choice"]) or "").strip())


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Review: {name}</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f6f6f4;color:#1d1d1b}}
header{{background:#fff;border-bottom:1px solid #ddd;padding:12px 24px;display:flex;gap:16px;align-items:center}}
main{{max-width:900px;margin:24px auto;padding:0 24px}}
.card{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:20px;margin-bottom:16px}}
.f{{margin:10px 0}} .f b{{display:block;font-size:12px;text-transform:uppercase;color:#777;letter-spacing:.04em}}
.f div{{white-space:pre-wrap}} .opts button{{font-size:18px;padding:10px 22px;margin-right:8px;border-radius:8px;
border:1px solid #999;background:#fff;cursor:pointer}} .opts button.on{{background:#1d1d1b;color:#fff}}
textarea,input{{width:100%;font:inherit;padding:6px;border:1px solid #bbb;border-radius:6px;box-sizing:border-box}}
.nav button{{padding:8px 16px}} .muted{{color:#777;font-size:14px}} .bar{{height:6px;background:#e5e5e0;border-radius:3px;flex:1}}
.bar i{{display:block;height:6px;background:#2f7d4a;border-radius:3px}}
</style></head><body>
<header><b>{name}</b><span class="muted" id="count"></span><div class="bar"><i id="prog"></i></div>
<label class="muted">Your name <input id="who" style="width:160px"></label></header>
<main><div class="card"><div class="muted" id="pos"></div><h3>{title}</h3><div id="show"></div></div>
<div class="card"><div class="opts" id="opts"></div><div id="texts"></div>
<p class="muted">Keys: 1/2/3 choose, ← → move. Saved to the sheet as you go.</p>
<div class="nav"><button id="prev">← Previous</button> <button id="next">Next →</button>
<button id="nextopen">Next undecided</button></div></div></main>
<script>
const T={token}, S={spec}; let rows=[], i=0;
const who=document.getElementById('who'); who.value=localStorage.getItem('reviewer')||'';
who.onchange=()=>localStorage.setItem('reviewer',who.value);
async function load(){{rows=(await (await fetch('/api/rows?t='+T)).json()).rows; i=rows.findIndex(r=>!r[S.choice]); if(i<0)i=0; draw();}}
function esc(s){{return String(s??'').replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]))}}
function draw(){{const r=rows[i]; const done=rows.filter(x=>x[S.choice]).length;
 document.getElementById('count').textContent=done+' of '+rows.length+' decided';
 document.getElementById('prog').style.width=(100*done/rows.length)+'%';
 document.getElementById('pos').textContent='Item '+(i+1)+' of '+rows.length+(r.case_id?' · '+r.case_id:'')+(r.id?' · '+r.id:'');
 document.getElementById('show').innerHTML=S.show.filter(k=>r[k]).map(k=>'<div class="f"><b>'+esc(k.replace(/_/g,' '))+'</b><div>'+esc(r[k])+'</div></div>').join('');
 document.getElementById('opts').innerHTML=S.options.map((o,n)=>'<button data-v="'+o+'" class="'+(r[S.choice]===o?'on':'')+'">'+(n+1)+'. '+o+'</button>').join('');
 document.querySelectorAll('#opts button').forEach(b=>b.onclick=()=>save({{[S.choice]:b.dataset.v}}));
 document.getElementById('texts').innerHTML=S.text.filter(k=>k in r).map(k=>'<div class="f"><b>'+esc(k.replace(/_/g,' '))
   +'</b><textarea rows="2" data-k="'+k+'">'+esc(r[k])+'</textarea></div>').join('');
 document.querySelectorAll('#texts textarea').forEach(t=>t.onchange=()=>save({{[t.dataset.k]:t.value}},true));}}
async function save(v,stay){{if(!who.value.trim()){{alert('Enter your name first (it is the sign-off).');who.focus();return;}}
 v.reviewer=who.value.trim(); Object.assign(rows[i],v);
 const res=await fetch('/api/save?t='+T,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{i:i,values:v}})}});
 if(!res.ok){{alert('Could not save: '+await res.text());return;}}
 if(!stay&&i<rows.length-1)i++; draw();}}
document.getElementById('prev').onclick=()=>{{if(i>0)i--;draw()}};
document.getElementById('next').onclick=()=>{{if(i<rows.length-1)i++;draw()}};
document.getElementById('nextopen').onclick=()=>{{const j=rows.findIndex((r,k)=>k>i&&!r[S.choice]);if(j>=0)i=j;draw()}};
document.onkeydown=e=>{{if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT')return;
 if(e.key==='ArrowLeft')document.getElementById('prev').click(); if(e.key==='ArrowRight')document.getElementById('next').click();
 const n=parseInt(e.key); if(n>=1&&n<=S.options.length)save({{[S.choice]:S.options[n-1]}});}};
load();
</script></body></html>"""


def make_server(store: SheetStore, host: str = "127.0.0.1", port: int = 0, token: str | None = None):
    token = token or secrets.token_urlsafe(16)
    spec = {k: store.spec[k] for k in ("choice", "options", "text", "show")}
    page = PAGE.format(name=html.escape(store.path.name), title=html.escape(store.spec["title"]),
                       token=json.dumps(token), spec=json.dumps(spec)).encode()

    class H(BaseHTTPRequestHandler):
        def _ok(self, body: bytes, ctype="application/json", code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            return (parse_qs(urlparse(self.path).query).get("t") or [""])[0] == token

        def do_GET(self):  # noqa: N802
            if not self._authorised():
                return self._ok(b"open the link printed by genai-evals label", "text/plain", 403)
            u = urlparse(self.path).path
            if u == "/":
                return self._ok(page, "text/html; charset=utf-8")
            if u == "/api/rows":
                return self._ok(json.dumps({"rows": store.rows}).encode())
            return self._ok(b"not found", "text/plain", 404)

        def do_POST(self):  # noqa: N802
            if not self._authorised() or urlparse(self.path).path != "/api/save":
                return self._ok(b"forbidden", "text/plain", 403)
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                i = int(d["i"])
                if not 0 <= i < len(store.rows):
                    raise ValueError("no such row")
                if not str((d.get("values") or {}).get("reviewer", "")).strip():
                    raise ValueError("a reviewer name is required")
                store.save(i, d.get("values") or {})
                return self._ok(json.dumps({"saved": i, "done": store.done()}).encode())
            except Exception as e:  # noqa: BLE001
                return self._ok(str(e).encode(), "text/plain", 400)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), H)
    return srv, f"http://{host}:{srv.server_port}/?t={token}"
