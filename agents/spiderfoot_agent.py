#!/usr/bin/env python3
import os
import sys
import time
import json
import subprocess
from pathlib import Path

# ---------- paths (case-aware) ----------
ROOT = Path(__file__).resolve().parents[1]
SF_PY = ROOT / "spiderfoot" / "sf.py"   # SpiderFoot entry script

DEFAULT_OUTBASE = ROOT / "out"
OUTBASE = Path(os.getenv("OUTBASE", str(DEFAULT_OUTBASE)))
OUTDIR_JSON = OUTBASE / "spiderfoot"
OUTDIR_TXT  = OUTBASE / "spiderfoot_txt"
OUTDIR_JSON.mkdir(parents=True, exist_ok=True)
OUTDIR_TXT.mkdir(parents=True, exist_ok=True)

# ---------- helpers ----------
def run_cmd(cmd, timeout=900):
    try:
        cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return cp.returncode, cp.stdout or "", cp.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "TimeoutExpired"

def slug(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-@+" else "_" for ch in s).strip("_")

# ---------- main ----------
def run_spiderfoot(target: str) -> int:
    if not SF_PY.exists():
        print(f"[SpiderFoot] Not found at {SF_PY}. Did you clone it to ./spiderfoot ?")
        return 127

    print(f"Running SpiderFoot on {target}...")
    cmd = [
        sys.executable, str(SF_PY),
        "-s", target,          # target (domain/email/ip/phone/etc.)
        "-m", "ALL",           # all modules
        "-o", "json",          # JSON (NDJSON) to stdout
        "-q"                   # quiet banners
    ]
    code, out, err = run_cmd(cmd)

    s = slug(target)
    ts = int(time.time())
    json_path = OUTDIR_JSON / f"{s}_{ts}.json"
    txt_path  = OUTDIR_TXT  / f"{s}_{ts}.txt"

    # SpiderFoot prints NDJSON; wrap it as a valid JSON array for easy parsing
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    array_text = "[\n" + ",\n".join(lines) + "\n]" if lines else "[]"
    json_path.write_text(array_text)

    # Human-readable preview
    preview = [
        f"SpiderFoot target: {target}",
        f"exit_code: {code}",
        f"stdout_lines: {len(lines)}",
        f"stderr_bytes: {len(err)}",
        "",
        "(first 80 lines)",
        "----------------------------------------",
    ]
    preview += (out.splitlines()[:80] if out.strip() else (["<no stdout>"] + (["", "stderr:", err] if err.strip() else [])))
    txt_path.write_text("\n".join(preview))

    print(f"✅ Saved JSON → {json_path}")
    print(f"✅ Saved TXT  → {txt_path}")

    if code != 0 and err.strip():
        print("[SpiderFoot stderr]\n" + err.strip())
    return code

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agents/spiderfoot_agent.py <target>")
        sys.exit(1)
    sys.exit(run_spiderfoot(sys.argv[1]))

