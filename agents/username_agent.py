#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from slugify import slugify

# ---- paths (case-aware) -----------------------------------------------------
# If run via run_case.py / mother_agent.py, OUTBASE is set; otherwise default to repo/out
DEFAULT_OUTBASE = Path(__file__).resolve().parents[1] / "out"
OUTBASE = Path(os.getenv("OUTBASE", str(DEFAULT_OUTBASE)))
OUTDIR_JSON = OUTBASE / "username"
OUTDIR_TXT  = OUTBASE / "username_txt"
OUTDIR_JSON.mkdir(parents=True, exist_ok=True)
OUTDIR_TXT.mkdir(parents=True, exist_ok=True)

# ---- helpers ----------------------------------------------------------------
def run_cmd(cmd, timeout=600):
    """Run a command and capture stdout/stderr."""
    try:
        cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return cp.returncode, cp.stdout or "", cp.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "TimeoutExpired"

def sherlock_cmd():
    """Prefer installed sherlock CLI; fallback to python -m sherlock."""
    cli = shutil.which("sherlock")
