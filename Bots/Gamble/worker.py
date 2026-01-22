import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple
import random
import psutil

# Ensure local imports work when running worker.py directly
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(THIS_DIR))

from games_core import run_game_task  # noqa: E402


def _project_root() -> Path:
    # BOT/Bots/Gamble/worker.py -> BOT/
    return Path(__file__).resolve().parents[2]


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_new_jsonl_lines(file_path: Path, offset_bytes: int) -> Tuple[List[Dict[str, Any]], int]:
    _ensure_file(file_path)
    new_items: List[Dict[str, Any]] = []
    with file_path.open("rb") as f:
        f.seek(offset_bytes, os.SEEK_SET)
        while True:
            line = f.readline()
            if not line:
                break
            offset_bytes += len(line)
            s = line.decode("utf-8", errors="replace").strip()
            if not s:
                continue
            try:
                new_items.append(json.loads(s))
            except Exception:
                # Ignore malformed lines
                continue
    return new_items, offset_bytes


def _append_jsonl(file_path: Path, obj: Dict[str, Any]) -> None:
    _ensure_file(file_path)
    with file_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main() -> None:
    root = _project_root()

    bus_dir = root / "ChatManager" / "bus"
    inbox = bus_dir / "gamble.inbox.jsonl"
    outbox = bus_dir / "gamble.outbox.jsonl"
    ack = bus_dir / "gamble.ack.jsonl"

    state_dir = root / "Bots" / "Gamble" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    offsets_path = state_dir / "offsets.json"

    offsets = _load_json(offsets_path, {"inbox_offset_bytes": 0})
    inbox_offset = int(offsets.get("inbox_offset_bytes", 0))
    # Single-worker lock (survives crashes): store PID in lock file and only
    # block if that PID is still running.
    lock_path = state_dir / "worker.lock"

    def _pid_alive(pid: int) -> bool:
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False

    if lock_path.exists():
        try:
            info = json.loads(lock_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            info = {}
        pid = int(info.get("pid", 0) or 0)
        if pid and _pid_alive(pid):
            print(f"[GambleWorker] worker.lock held by pid={pid}. Another worker is running. Exiting.")
            return
        # Stale lock (pid missing or not alive) => clear it
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            print("[GambleWorker] worker.lock exists but could not be removed. Exiting.")
            return

    # Create lock atomically
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(lock_fd, json.dumps({"pid": os.getpid(), "started_ts": int(time.time())}).encode("utf-8"))
    except FileExistsError:
        print("[GambleWorker] worker.lock exists. Another worker is running. Exiting.")
        return

    rng = random.Random()

    print("[GambleWorker] Started. Reading:", inbox)

    try:
        while True:
            tasks, inbox_offset = _read_new_jsonl_lines(inbox, inbox_offset)
            if tasks:
                offsets["inbox_offset_bytes"] = inbox_offset
                _atomic_write_json(offsets_path, offsets)

            for task in tasks:
                task_id = str(task.get("task_id", "")).strip()
                ts = int(time.time())

                try:
                    reply_payload = run_game_task(task, rng)

                    _append_jsonl(outbox, {
                        "type": "reply",
                        "task_id": task_id,
                        "ts": ts,
                        **reply_payload,
                    })

                    _append_jsonl(ack, {
                        "type": "ack",
                        "task_id": task_id,
                        "ts": ts,
                        "status": "ok",
                    })

                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    tb = traceback.format_exc(limit=5)

                    # Always output a reply on errors
                    _append_jsonl(outbox, {
                        "type": "reply",
                        "task_id": task_id,
                        "ts": ts,
                        "game": {
                            "name": str(task.get("action", "unknown")),
                            "bet": int(task.get("bet", 0) or 0),
                            "result_code": "ERROR",
                            "payout": 0
                        },
                        "messages": [f"🎰 Sorry {task.get('reply_name','there')} — the casino glitched. Try again."],
                        "overlay_events": [],
                        "blocking_ms": 0,
                        "error": err,
                    })

                    _append_jsonl(ack, {
                        "type": "ack",
                        "task_id": task_id,
                        "ts": ts,
                        "status": "error",
                        "error": err,
                        "trace": tb,
                    })

            time.sleep(0.10)

    finally:
        try:
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
