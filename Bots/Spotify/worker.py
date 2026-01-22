import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from spotify_core import (
    make_spotify,
    ensure_active_device,
    get_now_playing,
    get_queue,
    search_track_robust,
    fmt_track,
    add_to_queue,
    play,
    pause,
    skip,
    set_volume,
    clamp,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load a single shared .env file from /bot/.env (preferred)
bot_root = Path(__file__).resolve().parents[2]
shared_env = bot_root / ".env"
if shared_env.exists():
    load_dotenv(shared_env)
else:
    # Fallback (lets you run with a local env file if you want)
    load_dotenv(os.path.join(BASE_DIR, ".env"))

BUS_INBOX = os.path.normpath(os.path.join(BASE_DIR, os.getenv("BUS_INBOX", "../../ChatManager/bus/spotify.inbox.jsonl")))
BUS_OUTBOX = os.path.normpath(os.path.join(BASE_DIR, os.getenv("BUS_OUTBOX", "../../ChatManager/bus/spotify.outbox.jsonl")))
BUS_ACK = os.path.normpath(os.path.join(BASE_DIR, os.getenv("BUS_ACK", "../../ChatManager/bus/spotify.ack.jsonl")))

STATE_DIR = os.path.join(BASE_DIR, "state")
OFFSETS_FILE = os.path.join(STATE_DIR, "offsets.json")

# Active/Standby lock files (single workstation)
LEADER_LOCK_FILE = os.path.join(STATE_DIR, "leader.lock")
LEADER_HB_FILE = os.path.join(STATE_DIR, "leader_heartbeat.json")

LOCK_TTL_SEC = float(os.getenv("WORKER_LOCK_TTL_SEC", "8"))          # if no heartbeat for this long -> takeover
HEARTBEAT_EVERY_SEC = float(os.getenv("WORKER_HEARTBEAT_SEC", "1"))  # leader heartbeat cadence
POLL_SLEEP_SEC = float(os.getenv("WORKER_POLL_SEC", "0.08"))         # fast polling


def safe_print(msg: str) -> None:
    print(msg, flush=True)


def now_ms() -> int:
    return int(time.time() * 1000)


def load_json(path: str, default: Any) -> Any:
    try:
        if not os.path.exists(path):
            return default
        raw = open(path, "r", encoding="utf-8").read().strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default


def atomic_write_json(path: str, obj: Any) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def send_reply(task_id: str, messages: List[str]) -> None:
    append_jsonl(BUS_OUTBOX, {"type": "reply", "task_id": task_id, "ts": now_ms(), "messages": messages})


def send_ack(task_id: str, status: str, error: str = "") -> None:
    payload = {"type": "ack", "task_id": task_id, "ts": now_ms(), "status": status}
    if error:
        payload["error"] = error
    append_jsonl(BUS_ACK, payload)


def ensure_state_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


# --------- Active/Standby leader lock ----------
def _try_create_lock(lock_path: str, payload: Dict[str, Any]) -> bool:
    """
    Atomic create. Only one worker can create leader.lock.
    """
    ensure_state_dir()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def _read_lock(lock_path: str) -> Dict[str, Any]:
    try:
        if not os.path.exists(lock_path):
            return {}
        return json.loads(open(lock_path, "r", encoding="utf-8").read() or "{}")
    except Exception:
        return {}


def _read_hb(hb_path: str) -> Dict[str, Any]:
    try:
        if not os.path.exists(hb_path):
            return {}
        return json.loads(open(hb_path, "r", encoding="utf-8").read() or "{}")
    except Exception:
        return {}


def _hb_age_sec() -> float:
    hb = _read_hb(LEADER_HB_FILE)
    ts = int(hb.get("heartbeat_ms", 0) or 0)
    if ts <= 0:
        return 1e9
    return max(0.0, (now_ms() - ts) / 1000.0)


def _steal_lock_if_stale(my_payload: Dict[str, Any]) -> bool:
    # If no lock, just try to create
    if not os.path.exists(LEADER_LOCK_FILE):
        return _try_create_lock(LEADER_LOCK_FILE, my_payload)

    # If heartbeat stale, steal
    if _hb_age_sec() > LOCK_TTL_SEC:
        try:
            # best-effort cleanup
            try:
                os.remove(LEADER_LOCK_FILE)
            except Exception:
                pass
            try:
                os.remove(LEADER_HB_FILE)
            except Exception:
                pass
        except Exception:
            pass
        return _try_create_lock(LEADER_LOCK_FILE, my_payload)

    return False


def leader_heartbeat(my_payload: Dict[str, Any]) -> None:
    atomic_write_json(LEADER_HB_FILE, {
        "heartbeat_ms": now_ms(),
        "pid": my_payload.get("pid"),
        "role": my_payload.get("role"),
        "instance": my_payload.get("instance"),
    })


# --------- Inbox reading (byte offsets; update AFTER each task) ----------
def iter_jsonl_from_offset(path: str, offset_bytes: int):
    if not os.path.exists(path):
        return
    with open(path, "rb") as f:
        try:
            f.seek(offset_bytes)
        except Exception:
            f.seek(0)
            offset_bytes = 0

        while True:
            line = f.readline()
            if not line:
                break
            new_off = f.tell()
            s = line.decode("utf-8", errors="replace").strip()
            if not s:
                offset_bytes = new_off
                continue
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    yield obj, new_off
                else:
                    yield None, new_off
            except Exception:
                yield None, new_off


def handle_task(sp, task: Dict[str, Any]) -> Tuple[List[str], str]:
    action = (task.get("action") or "").strip().lower()
    args = (task.get("args") or "").strip()

    if action == "np":
        now_str, url = get_now_playing(sp)
        if not now_str:
            return ["🎵 Nothing is currently playing."], "ok"
        if url:
            return [f"🎶 Now playing: {now_str} — {url}"], "ok"
        return [f"🎶 Now playing: {now_str}"], "ok"

    if action == "queue":
        limit = 5
        if args:
            try:
                limit = clamp(int(args), 1, 20)
            except Exception:
                limit = 5
        now_str, up_next = get_queue(sp, limit)
        if not now_str and not up_next:
            return ["🎵 Nothing is currently playing (or queue not available)."], "ok"
        if not up_next:
            if now_str:
                return [f"🎶 Now playing: {now_str} (queue list not available)"], "ok"
            return ["Queue list not available."], "ok"
        lines = [f"🎶 Now: {now_str}" if now_str else "🎶 Now: (unknown)"]
        for i, t in enumerate(up_next, 1):
            lines.append(f"{i}) {t}")
        return [" | ".join(lines)], "ok"

    if action == "sr":
        if not args:
            return ["Usage: sr <song name or spotify link>"], "ok"

        ok_dev, err = ensure_active_device(sp)
        if not ok_dev:
            return [f"⚠️ {err}"], "error"

        track = search_track_robust(sp, args)
        if not track:
            return ["❌ Couldn’t find that track on Spotify."], "ok"

        uri = track.get("uri")
        if not uri:
            return ["❌ Track found but missing URI."], "error"

        ok, err = add_to_queue(sp, uri)
        if not ok:
            return [f"❌ {err}"], "error"

        return [f"✅ Queued: {fmt_track(track)}"], "ok"

    if action == "skip":
        ok, err = skip(sp)
        if not ok:
            return [f"❌ {err}"], "error"
        return ["⏭️ Skipped."], "ok"

    if action == "play":
        ok, err = play(sp)
        if not ok:
            return [f"❌ {err}"], "error"
        return ["▶️ Playback started."], "ok"

    if action == "pause":
        ok, err = pause(sp)
        if not ok:
            return [f"❌ {err}"], "error"
        return ["⏸️ Paused."], "ok"

    if action == "vol":
        try:
            v = clamp(int(args), 0, 100)
        except Exception:
            return ["Usage: vol <0-100>"], "ok"
        ok, err = set_volume(sp, v)
        if not ok:
            return [f"❌ {err}"], "error"
        return [f"🔊 Volume set to {v}%."], "ok"

    return [f"⚠️ Unknown action: {action}"], "error"


def main() -> None:
    ensure_state_dir()

    # Role/instance for primary/secondary behavior
    instance = str(os.getenv("CHAT_SUPERVISOR_INSTANCE", "0"))
    role = (os.getenv("WORKER_ROLE") or "").strip().lower()
    if role not in ("primary", "secondary"):
        role = "primary" if instance == "0" else "secondary"

    # Offsets migration:
    offsets = load_json(OFFSETS_FILE, {})
    if not isinstance(offsets, dict):
        offsets = {}

    # Use byte offsets; if legacy exists, skip existing to avoid replay
    if "inbox_offset_bytes" in offsets:
        inbox_off = int(offsets.get("inbox_offset_bytes", 0) or 0)
    else:
        # Legacy "inbox" might exist (text cookie). Don’t reuse it; skip current file content.
        try:
            inbox_off = os.path.getsize(BUS_INBOX) if os.path.exists(BUS_INBOX) else 0
        except Exception:
            inbox_off = 0
        offsets["inbox_offset_bytes"] = inbox_off
        atomic_write_json(OFFSETS_FILE, offsets)

    safe_print(f"[SpotifyWorker] role={role} instance={instance}")
    safe_print(f"[SpotifyWorker] inbox={BUS_INBOX}")
    safe_print(f"[SpotifyWorker] outbox={BUS_OUTBOX}")
    safe_print(f"[SpotifyWorker] ack={BUS_ACK}")
    safe_print(f"[SpotifyWorker] lock_ttl={LOCK_TTL_SEC}s hb_every={HEARTBEAT_EVERY_SEC}s poll={POLL_SLEEP_SEC}s")

    my_payload = {
        "pid": os.getpid(),
        "role": role,
        "instance": instance,
        "started_ms": now_ms(),
    }

    # Primary tries immediately; secondary waits slightly so primary wins cleanly
    if role == "secondary":
        time.sleep(0.6)

    is_leader = False
    last_hb = 0.0
    sp = None

    while True:
        try:
            # Acquire or maintain leadership
            if not is_leader:
                if _try_create_lock(LEADER_LOCK_FILE, my_payload) or _steal_lock_if_stale(my_payload):
                    is_leader = True
                    safe_print("[SpotifyWorker] ✅ LEADER (ACTIVE) — Spotify API enabled")
                    sp = make_spotify()
                    safe_print("[SpotifyWorker] Ready.")
                    leader_heartbeat(my_payload)
                    last_hb = time.time()
                else:
                    # Standby mode
                    safe_print("[SpotifyWorker] 💤 STANDBY — waiting (primary active)")
                    time.sleep(0.5)
                    continue

            # Leader heartbeat
            if (time.time() - last_hb) >= HEARTBEAT_EVERY_SEC:
                leader_heartbeat(my_payload)
                last_hb = time.time()

            # If someone stole lock (shouldn’t happen unless split brain), drop to standby
            if os.path.exists(LEADER_LOCK_FILE):
                lock = _read_lock(LEADER_LOCK_FILE)
                if int(lock.get("pid", -1) or -1) != os.getpid():
                    is_leader = False
                    safe_print("[SpotifyWorker] ⚠️ Lost leadership — switching to STANDBY")
                    time.sleep(0.5)
                    continue

            # Process inbox (leader only)
            progressed = False
            for rec, new_off in iter_jsonl_from_offset(BUS_INBOX, inbox_off):
                inbox_off = new_off
                offsets["inbox_offset_bytes"] = inbox_off
                atomic_write_json(OFFSETS_FILE, offsets)
                progressed = True

                if not isinstance(rec, dict):
                    continue
                if (rec.get("type") or "").lower() != "task":
                    continue

                task_id = (rec.get("task_id") or "").strip()
                if not task_id:
                    continue

                try:
                    msgs, status = handle_task(sp, rec)
                    send_reply(task_id, msgs)
                    send_ack(task_id, "ok" if status == "ok" else "error", "" if status == "ok" else (msgs[0] if msgs else "error"))
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    send_reply(task_id, [f"❌ {err}"])
                    send_ack(task_id, "error", err)

            # Tight loop when active work is happening; slightly slower when idle
            time.sleep(0.01 if progressed else POLL_SLEEP_SEC)

        except Exception as e:
            safe_print(f"[SpotifyWorker] loop error: {type(e).__name__}: {e}")
            time.sleep(0.2)


if __name__ == "__main__":
    main()
