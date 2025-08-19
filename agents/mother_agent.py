#!/usr/bin/env python3
import os
import re
import sys
import time
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Paths (works whether file is in /agents or repo root) -------------------
ROOT = Path(__file__).resolve().parents[1] if Path(__file__).parent.name == "agents" else Path(__file__).parent
PY = sys.executable
REPO_REPORTS = ROOT / "reports"
REPO_REPORTS.mkdir(exist_ok=True)

CASE_DIR = Path(os.getenv("CASE_DIR", str(ROOT)))
TARGETS_TXT = CASE_DIR / "targets.txt"

def find_agent(fname: str) -> Path:
    for p in (ROOT / "agents" / fname, ROOT / fname):
        if p.exists():
            return p
    raise FileNotFoundError(f"Agent not found: {fname}")

USERNAME_AGENT = find_agent("username_agent.py")
SPIDERFOOT_AGENT = find_agent("spiderfoot_agent.py")
SUMMARY_AGENT = find_agent("summary_agent.py")

# --- Logging -----------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mother_agent")

# --- Subprocess helper ------------------------------------------------------
def sh(args: List[str], env: Optional[Dict[str, str]] = None, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, text=True, capture_output=True, env=env, timeout=timeout)

# --- Config & Task tracking -------------------------------------------------
class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class TaskResult:
    target: str
    agent: str
    status: TaskStatus
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    returncode: Optional[int] = None
    error: Optional[str] = None

@dataclass
class Config:
    case_dir: Path
    report_dir: Path
    agents: Dict[str, Path]
    max_retries: int = 3
    parallel: bool = False
    worker_count: int = 4

    @classmethod
    def load(cls, argv: List[str]) -> "Config":
        parallel = ("--parallel" in argv) or ("-p" in argv)
        workers = 4
        if "--workers" in argv:
            try:
                idx = argv.index("--workers")
                workers = int(argv[idx + 1])
            except Exception:
                pass
        return cls(
            case_dir=CASE_DIR,
            report_dir=REPO_REPORTS,
            agents={
                "username": USERNAME_AGENT,
                "spiderfoot": SPIDERFOOT_AGENT,
                "summary": SUMMARY_AGENT,
            },
            max_retries=int(os.getenv("MOTHER_MAX_RETRIES", "3")),
            parallel=parallel,
            worker_count=workers,
        )

# --- Routing / executor -----------------------------------------------------
def execute_agent(agent_path: Path, args: List[str], cfg: Config, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    last_exc = None
    for attempt in range(1, cfg.max_retries + 1):
        try:
            logger.info("Running %s %s (attempt %d/%d)", agent_path.name, " ".join(args), attempt, cfg.max_retries)
            cp = sh([PY, str(agent_path), *args], env=env)
            if cp.stdout:
                logger.info("[%s stdout] %s", agent_path.stem, cp.stdout.strip())
            if cp.stderr:
                logger.warning("[%s stderr] %s", agent_path.stem, cp.stderr.strip())
            return cp
        except subprocess.TimeoutExpired as te:
            last_exc = te
            logger.error("Timeout running %s: %s", agent_path.name, te)
        except Exception as e:
            last_exc = e
            logger.exception("Error running %s", agent_path.name)
        if attempt < cfg.max_retries:
            delay = 2 ** (attempt - 1)
            logger.info("Retrying %s in %ds...", agent_path.name, delay)
            time.sleep(delay)
    raise last_exc if last_exc is not None else RuntimeError("Unknown execution failure")

def run_username(value: str, cfg: Config, env: Optional[Dict[str, str]] = None) -> TaskResult:
    t = TaskResult(target=value, agent="username", status=TaskStatus.RUNNING, start_time=datetime.now())
    try:
        cp = execute_agent(cfg.agents["username"], [value], cfg, env=env)
        t.end_time = datetime.now()
        t.returncode = cp.returncode
        t.status = TaskStatus.COMPLETED if cp.returncode == 0 else TaskStatus.FAILED
        if cp.returncode != 0:
            t.error = f"exit {cp.returncode}"
    except Exception as e:
        t.end_time = datetime.now()
        t.status = TaskStatus.FAILED
        t.error = str(e)
    logger.info("Username task %s → %s", value, t.status.value)
    return t

def run_spiderfoot(kind: str, value: str, cfg: Config, env: Optional[Dict[str, str]] = None) -> TaskResult:
    t = TaskResult(target=f"{kind}:{value}", agent="spiderfoot", status=TaskStatus.RUNNING, start_time=datetime.now())
    try:
        cp = execute_agent(cfg.agents["spiderfoot"], [value], cfg, env=env)
        t.end_time = datetime.now()
        t.returncode = cp.returncode
        t.status = TaskStatus.COMPLETED if cp.returncode == 0 else TaskStatus.FAILED
        if cp.returncode != 0:
            t.error = f"exit {cp.returncode}"
    except Exception as e:
        t.end_time = datetime.now()
        t.status = TaskStatus.FAILED
        t.error = str(e)
    logger.info("SpiderFoot task %s → %s", t.target, t.status.value)
    return t

# --- Prompt helpers ---------------------------------------------------------
def guess_kind(v: str) -> str:
    v = v.strip()
    if re.fullmatch(r".+@.+\..+", v):
        return "email"
    if re.fullmatch(r"\+?\d[\d\s().-]{6,}$", v):
        return "phone"
    if "." in v and " " not in v:
        return "domain"
    return "username"

def gather_targets_interactive() -> List[Tuple[str, str]]:
    print("\nInteractive mode. Enter one target per line.")
    print("Examples:  elonmusk  |  example.com  |  user@mail.com  |  +1 555 123 4567")
    print("You can also type 'kind: value' (e.g., 'email: a@b.com').")
    print("Press ENTER on a blank line when you’re done.\n")
    items: List[Tuple[str, str]] = []
    while True:
        raw = input("> ").strip().strip(",")
        if not raw:
            break
        if ":" in raw:
            k, v = [p.strip() for p in raw.split(":", 1)]
            items.append((k.lower(), v))
        else:
            items.append((guess_kind(raw), raw))
    return items

def maybe_save_targets(pairs: List[Tuple[str, str]]):
    if not pairs:
        return
    ans = input("\nSave these targets to targets.txt? (y/n): ").strip().lower()
    if ans != "y":
        return
    mode = input("Overwrite or append? (o/a) [o]: ").strip().lower() or "o"
    text = "\n".join(f"{k}: {v}" for k, v in pairs) + "\n"
    if mode == "a":
        existing = TARGETS_TXT.read_text() if TARGETS_TXT.exists() else ""
        TARGETS_TXT.write_text(existing + text)
        print(f"✅ Appended {len(pairs)} targets to {TARGETS_TXT}")
    else:
        TARGETS_TXT.write_text(text)
        print(f"✅ Saved {len(pairs)} targets to {TARGETS_TXT}")

def ask_case_name() -> str:
    raw = input("\nCase name (optional, e.g., 'claim-0812' or 'levelup'): ").strip()
    if not raw:
        return "case-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in raw).strip().replace(" ", "_")
    return safe or ("case-" + datetime.now().strftime("%Y%m%d-%H%M%S"))

def load_targets_from_file(path: Path) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    if not path.exists():
        return pairs
    for l in path.read_text().splitlines():
        l = l.strip()
        if not l or ":" not in l:
            continue
        k, v = [p.strip() for p in l.split(":", 1)]
        pairs.append((k.lower(), v))
    return pairs

# --- Main flow --------------------------------------------------------------
def main():
    cfg = Config.load(sys.argv)
    use_prompt = ("--prompt" in sys.argv) or ("-i" in sys.argv)

    if use_prompt or not TARGETS_TXT.exists():
        targets = gather_targets_interactive()
        if not targets:
            logger.info("No targets entered. Bye.")
            return
        maybe_save_targets(targets)
    else:
        targets = load_targets_from_file(TARGETS_TXT)
        if not targets:
            logger.info("%s is empty. Re-run with --prompt to enter targets interactively.", TARGETS_TXT)
            return

    # Ask for a case name → report filename
    case_name = ask_case_name()
    report_name = f"{case_name}.txt"
    env = os.environ.copy()
    env["REPORT_NAME"] = report_name  # summary_agent will use this
    logger.info("Report will be saved to: %s", cfg.report_dir / report_name)

    # Prepare tasks
    tasks: List[Tuple[str, str]] = []  # (kind, value)
    for kind, value in targets:
        tasks.append((kind, value))

    results: List[TaskResult] = []

    def dispatch_task(kind: str, value: str) -> TaskResult:
        if kind == "username":
            return run_username(value, cfg, env=env)
        elif kind in {"domain", "email", "ip", "phone"}:
            return run_spiderfoot(kind, value, cfg, env=env)
        else:
            tr = TaskResult(target=value, agent="unknown", status=TaskStatus.FAILED, start_time=datetime.now(), end_time=datetime.now(), error=f"unknown type {kind}")
            logger.warning("Unknown type '%s', skipping %s", kind, value)
            return tr

    # Run tasks (parallel or sequential)
    if cfg.parallel and tasks:
        logger.info("Running tasks in parallel with %d workers", cfg.worker_count)
        with ThreadPoolExecutor(max_workers=cfg.worker_count) as ex:
            future_map = {ex.submit(dispatch_task, k, v): (k, v) for k, v in tasks}
            for fut in as_completed(future_map):
                try:
                    res = fut.result()
                except Exception as e:
                    k, v = future_map[fut]
                    logger.exception("Task %s:%s raised", k, v)
                    res = TaskResult(target=f"{k}:{v}", agent="unknown", status=TaskStatus.FAILED, start_time=datetime.now(), end_time=datetime.now(), error=str(e))
                results.append(res)
    else:
        for kind, value in tasks:
            results.append(dispatch_task(kind, value))

    # Build summary
    logger.info("Building summary…")
    try:
        cp = execute_agent(cfg.agents["summary"], [], cfg, env=env)
        if cp.stdout:
            logger.info(cp.stdout.strip())
        if cp.stderr:
            logger.warning(cp.stderr.strip())
    except Exception as e:
        logger.exception("Summary generation failed: %s", e)

    # Final status
    success_count = sum(1 for r in results if r.status == TaskStatus.COMPLETED)
    logger.info("Investigation complete: %d/%d tasks successful", success_count, len(results))
    logger.info("Report: %s", cfg.report_dir / report_name)

if __name__ == "__main__":
    main()
