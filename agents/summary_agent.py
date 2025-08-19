#!/usr/bin/env python3
import os
import re
import csv
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Iterable, Iterator, Any, Dict, List

# ---------- locate repo root no matter where this file lives ----------
HERE = Path(__file__).resolve()
REPO = HERE.parents[1] if HERE.parent.name == "agents" else HERE.parent

# ---------- inputs/outputs ----------
OUT      = Path(os.getenv("OUTBASE", str(REPO / "out")))
REPORTS  = REPO / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

REPORT_NAME = os.getenv("REPORT_NAME") or f"investigation_{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
REPORT_STEM = Path(REPORT_NAME).stem  # for csv sidecar

URL_RE = re.compile(r"https?://[^\s\"'>)]+", re.I)

# ---------- helpers ----------
def json_lines_to_list(raw: str) -> List[Any]:
    """Parse NDJSON into list of Python objects (dicts/lists), skip bad lines."""
    items: List[Any] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
            items.append(obj)
        except Exception:
            pass
    return items

def load_sf_file(path: Path) -> Any:
    """Load a SpiderFoot JSON file which may be JSON array, dict, or NDJSON."""
    raw = path.read_text().strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return json_lines_to_list(raw)

def iter_records(obj: Any) -> Iterator[Dict]:
    """Yield dict records from possibly nested lists/dicts."""
    if isinstance(obj, dict):
        yield obj
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            yield from iter_records(x)

def get_first_str(d: dict, keys: Iterable[str]) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (str, int, float)):
            s = str(v).strip()
            if s:
                return s
    return ""

def extract_first_url(d: dict) -> str:
    url = get_first_str(d, ("url","link","website","permalink","resource","source","origin"))
    if url.startswith(("http://","https://")):
        return url
    for v in d.values():
        if isinstance(v, str):
            m = URL_RE.search(v)
            if m:
                return m.group(0)
    return ""

def extract_value(d: dict) -> str:
    s = get_first_str(
        d,
        (
            "data","content","value","text","target",
            "host","domain","email","phone","username",
            "title","name"
        ),
    )
    if s:
        return s
    for v in d.values():
        if isinstance(v, str) and 2 <= len(v) <= 500:
            return v
    return ""

def truncate(s: str, n: int = 140) -> str:
    return s if len(s) <= n else (s[: n - 1] + "…")

# ---------- collect ----------
def collect():
    ctx = {"username": [], "spiderfoot_files": []}

    udir = OUT / "username"
    if udir.exists():
        for f in sorted(udir.glob("*.json")):
            try:
                ctx["username"].append(json.loads(f.read_text()))
            except Exception as e:
                ctx["username"].append({"target": f.name, "error": str(e)})

    sfdir = OUT / "spiderfoot"
    if sfdir.exists():
        for f in sorted(sfdir.glob("*.json")):
            ctx["spiderfoot_files"].append(f)

    return ctx

# ---------- build report ----------
def build():
    ctx = collect()
    lines = ["OSINT Summary Report", "=" * 24, ""]

    # ---- Usernames (Sherlock) ----
    lines += ["USERNAMES", "-" * 20]
    if not ctx["username"]:
        lines += ["No username data.", ""]
    else:
        for item in ctx["username"]:
            tgt = item.get("target") or item.get("tools", {}).get("sherlock", {}).get("target", "(unknown)")
            lines.append(f"Target: {tgt}")
            tool = item.get("tools", {}).get("sherlock", {})
            code = item.get("exit_code", tool.get("code", "?"))
            lines.append(f"  Tool: Sherlock (exit code: {code})")
            out = tool.get("stdout", "") or ""
            for L in out.splitlines()[:25]:
                lines.append("    " + L)
            lines.append("")

    # ---- SpiderFoot results ----
    details_rows = []  # full dump for CSV
    type_counts  = defaultdict(int)
    module_counts = defaultdict(int)

    if not ctx["spiderfoot_files"]:
        lines += ["SPIDERFOOT RESULTS", "-" * 20, "No SpiderFoot data.", ""]
    else:
        for jf in ctx["spiderfoot_files"]:
            root = load_sf_file(jf)
            for it in iter_records(root):
                if not isinstance(it, dict):
                    continue
                tp  = (get_first_str(it, ("type","_type")) or "unknown").strip()
                mod = get_first_str(it, ("module","_module","source"))
                val = extract_value(it)
                url = extract_first_url(it)

                # counters
                type_counts[tp] += 1
                if mod:
                    module_counts[mod] += 1

                # store row (full dump for CSV)
                details_rows.append({
                    "type": tp or "unknown",
                    "value": val,
                    "url": url,
                    "module": mod,
                    "source_file": jf.name
                })

        # Summary section
        lines += ["SPIDERFOOT RESULTS", "-" * 20]
        if type_counts:
            for tp, cnt in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:20]:
                lines.append(f"{tp}: {cnt}")
        else:
            lines.append("No parsed items.")
        lines.append("")

        if module_counts:
            lines += ["Top Modules", "-" * 12]
            for mod, cnt in sorted(module_counts.items(), key=lambda x: x[1], reverse=True)[:15]:
                lines.append(f"{mod}: {cnt}")
            lines.append("")

        # Detail samples (human-readable)
        if details_rows:
            lines += ["SPIDERFOOT DETAILS (samples)", "-" * 30]
            for r in details_rows[:30]:
                line = f"- [{r['type']}] {truncate(r['value'] or '', 120)}"
                if r["url"]:
                    line += f"  ({r['url']})"
                if r["module"]:
                    line += f"  <{r['module']}>"
                lines.append(line)
            lines.append("")
        else:
            lines += ["No detailed items parsed.", ""]

    # --- write text summary
    out_txt = REPORTS / REPORT_NAME
    out_txt.write_text("\n".join(lines))

    # --- write CSV with all details
    out_csv = REPORTS / f"{REPORT_STEM}_details.csv"
    if details_rows:
        with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["type","value","url","module","source_file"])
            w.writeheader()
            w.writerows(details_rows)

    print(f"Saved report -> {out_txt}")
    if details_rows:
        print(f"Saved details -> {out_csv}")

if __name__ == "__main__":
    build()