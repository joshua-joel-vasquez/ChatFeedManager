import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from shared.logging_setup import setup_logging


def load_cfg(chatmanager_dir: Path) -> dict:
    return json.loads((chatmanager_dir / "commands.txt").read_text(encoding="utf-8"))


def find_worker_scripts(bot_dir: Path, bot_ids: list[str]) -> list[Path]:
    """Finds worker.py scripts for enabled bots.

    Looks in both:
      - <bot_dir>/Bots/<BotName>/worker.py (legacy)
      - <bot_dir>/<BotName>/worker.py (sibling folders like Spotify/, etc.)
    """

    scripts: list[Path] = []
    candidates: list[Path] = []

    if (bot_dir / "Bots").exists():
        candidates.append(bot_dir / "Bots")
    candidates.append(bot_dir)

    for root in candidates:
        if not root.exists() or not root.is_dir():
            continue
        dirs = [p for p in root.iterdir() if p.is_dir()]
        for bot_id in bot_ids:
            match = None
            for d in dirs:
                if d.name.lower() == bot_id.lower():
                    match = d
                    break
            if match:
                w = match / "worker.py"
                if w.exists():
                    scripts.append(w)

    # unique
    uniq = []
    seen = set()
    for s in scripts:
        if str(s) not in seen:
            uniq.append(s)
            seen.add(str(s))
    return uniq


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-workers",
        action="store_true",
        help="Do not start worker bots (only ingestor/router/emitter).",
    )
    args = parser.parse_args()

    chatmanager_dir = Path(__file__).resolve().parent
    services_dir = chatmanager_dir / "services"
    bot_dir = chatmanager_dir.parent  # /bot

    cfg = load_cfg(chatmanager_dir)
    log = setup_logging("launcher", cfg, chatmanager_dir)

    enabled_bot_ids: list[str] = []
    for b in (cfg.get("bots", []) or []):
        try:
            if b.get("enabled", True) is False:
                continue
            bid = str(b.get("id", "")).strip().lower()
            if bid:
                enabled_bot_ids.append(bid)
        except Exception:
            continue

    services = [
        services_dir / "ingestor.py",
        services_dir / "router_bank.py",
        services_dir / "emitter.py",
    ]

    worker_scripts = [] if args.no_workers else find_worker_scripts(bot_dir, enabled_bot_ids)

    procs: list[subprocess.Popen] = []

    def start(script: Path) -> None:
        """Start a Python child process.

        Important: microservices live in ChatManager/services and import from `shared/`.
        If we launch them by file path (services/ingestor.py), Python sets sys.path[0]
        to the services folder, and `import shared` fails.

        So for microservices, we launch them as modules: `python -m services.ingestor`.
        """

        if not script.exists():
            log.warning("Missing script: %s", str(script))
            return

        cmd = [sys.executable]
        if script.parent.resolve() == services_dir.resolve():
            cmd += ["-m", f"services.{script.stem}"]
        else:
            cmd += [str(script)]

        p = subprocess.Popen(cmd, cwd=str(chatmanager_dir))
        procs.append(p)
        log.info("Started: %s (pid=%s)", script.name, p.pid)

    log.info("Starting microservices...")
    for s in services:
        start(s)

    if worker_scripts:
        log.info("Starting workers...")
        for w in worker_scripts:
            start(w)
    else:
        if not args.no_workers:
            log.info("No worker.py scripts found (this is OK if you run bots separately).")

    log.info("Running. Ctrl+C to stop.")
    try:
        while True:
            for p in list(procs):
                rc = p.poll()
                if rc is not None:
                    log.error("Process exited pid=%s code=%s. Shutting down.", p.pid, rc)
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("Stopping...")
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(0.8)
        for p in procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass
        log.info("Stopped.")


if __name__ == "__main__":
    main()
