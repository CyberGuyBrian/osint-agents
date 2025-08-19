#!/usr/bin/env python3
import os, re, sys, subprocess, time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
RUNS = ROOT / "runs"
RUNS.mkdir(exist_ok=True)

PY = sys.executable

def guess_kind(v: str) -> str:
    v = v.strip()
    if re.fullmatch(r".+@.+\..+", v): return "email"
    if re.fullmatch(r"\+?\d[\d\s().-]{6,}$", v): return "phone"
    if "." in v and " " not in v: return "domain"
    return "username"

def main():
    if len(sys.argv) < 3:
        print("Usage:\n  python run_case.py <case_id> <target1> [<target2> ...]")
        print("Examples:\n  python run_case.py levelup fastreply@leveluphomeinspections.com levelupinspect leveluphomeinspections.com")
        print("  python run_case.py claim-0812 \"email: a@b.com\" \"domain: example.com\"")
        sys.exit(1)

    case_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", sys.argv[1]).strip("-")
    case_dir = RUNS / case_id
    out_base = case_dir / "out"
    case_dir.mkdir(parents=True, exist_ok=True)
    out_base.mkdir(parents=True, exist_ok=True)

    # Build a fresh targets.txt for THIS case
    targets = []
    for raw in sys.argv[2:]:
        raw = raw.strip().strip(",")
        if ":" in raw:
            k, v = [p.strip() for p in raw.split(":", 1)]
            targets.append(f"{k.lower()}: {v}")
        else:
            k = guess_kind(raw)
            targets.append(f"{k}: {raw}")
    (case_dir / "targets.txt").write_text("\n".join(targets) + "\n")

    print(f"[Run] Case: {case_id}")
    print("[Run] Targets:")
    for t in targets: print("  -", t)

    # Environment tells mother/agents where to read/write
    env = os.environ.copy()
    env["CASE_DIR"] = str(case_dir)
    env["OUTBASE"] = str(out_base)

    # Run mother
    cp = subprocess.run([PY, str(ROOT / "mother_agent.py")], text=True, env=env)
    print("\n[Run] Done. Report:")
    rep = case_dir / "report.txt"
    if rep.exists():
        print(rep)
        # also update a convenience symlink/copy
        (ROOT / "reports").mkdir(exist_ok=True)
        (ROOT / "reports" / "latest.txt").write_text(rep.read_text())

if __name__ == "__main__":
    main()
