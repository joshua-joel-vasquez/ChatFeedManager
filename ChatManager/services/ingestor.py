import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# When this script is executed directly (python services/ingestor.py), Python's
# import root becomes the services/ folder and `import shared` fails. Ensure the
# ChatManager/ directory is on sys.path.
_CHATMANAGER_DIR = Path(__file__).resolve().parents[1]
if str(_CHATMANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(_CHATMANAGER_DIR))

from shared.logging_setup import setup_logging


_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(s: Any) -> Any:
    """Expand ${VARS} inside strings using os.environ (missing vars -> "")."""
    if not isinstance(s, str):
        return s
    return _ENV_RE.sub(lambda m: os.getenv(m.group(1), ""), s)


def resolve_from_bot_root(p: str) -> Path:
    path = Path(p).expanduser()
    if path.is_absolute():
        return path
    bot_root = Path(__file__).resolve().parents[2]
    return (bot_root / path).resolve()


def ensure_file(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text("", encoding="utf-8")


def load_json(p: Path, default: Any) -> Any:
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def atomic_write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def append_jsonl(p: Path, obj: Dict[str, Any]) -> None:
    ensure_file(p)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_new_jsonl(p: Path, offset_bytes: int) -> Tuple[List[Dict[str, Any]], int]:
    ensure_file(p)
    items: List[Dict[str, Any]] = []
    with p.open("rb") as f:
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
                rec = json.loads(s)
                if isinstance(rec, dict):
                    items.append(rec)
            except Exception:
                continue
    return items, offset_bytes


def detect_user_tier(user: Dict[str, Any]) -> str:
    if user.get("isBroadcaster") or user.get("isStreamer") or user.get("isOwner"):
        return "BROADCASTER"
    if user.get("isMod") or user.get("isModerator"):
        return "MOD"
    if user.get("isVip") or user.get("isVIP"):
        return "VIP"
    if user.get("isSub") or user.get("isSubscriber") or user.get("subscriber"):
        return "SUB"
    return "EVERYONE"


def choose_reply_name(user: Dict[str, Any]) -> str:
    for k in ("name", "displayName", "username", "handle", "uniqueId", "nickname"):
        v = user.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in ("id", "userId", "uid"):
        v = user.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    key = user.get("key")
    if isinstance(key, str) and key.strip():
        if ":" in key:
            tail = key.split(":", 1)[1]
            if tail:
                return tail
        return key.strip()
    return "User"


def stable_user_key(platform: str, user: Dict[str, Any]) -> str:
    platform = (platform or "unknown").lower().strip()
    v = user.get("key")
    if isinstance(v, str) and v.strip():
        raw = v.strip()
        # Normalize keys that already include a platform prefix (common for
        # SSN/unified feeds), so we don't accidentally produce values like:
        #   twitch:twitch:username
        # which can break cooldowns and deduplication.
        if raw.lower().startswith(platform + ":"):
            return raw
        # If the key already contains a prefix for another platform, keep the
        # full value but still scope it under the current platform for
        # stability.
        # Example: platform="twitch", raw="tiktok:abc" -> twitch:tiktok:abc
        return f"{platform}:{raw}"
    for k in ("id", "userId", "uid", "uniqueId"):
        v = user.get(k)
        if v is not None and str(v).strip():
            return f"{platform}:{str(v).strip()}"
    for k in ("name", "displayName", "username", "handle"):
        v = user.get(k)
        if isinstance(v, str) and v.strip():
            return f"{platform}:{v.strip()}"
    return f"{platform}:unknown"


def _fingerprint_msg(m: Dict[str, Any]) -> str:
    platform = str(m.get("platform") or m.get("source") or "unknown").strip().lower()
    user = m.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    ukey = stable_user_key(platform, user)
    ts = int(m.get("ts", 0) or 0)
    text = m.get("message", m.get("text", ""))
    if not isinstance(text, str):
        text = ""
    return f"{platform}|{ukey}|{ts}|{text}"[:800]


def _read_unified_feed(chat_file: Path) -> Dict[str, Any]:
    txt = chat_file.read_text(encoding="utf-8", errors="replace")
    obj = json.loads(txt)
    if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
        return obj
    return {}


def read_new_records(chat_file: Path, offsets: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Reads new incoming messages from either:

    1) Unified chat feed JSON:
       { updatedTs: ..., messages: [...] }

    2) JSONL file (append-only): one JSON object per line

    Returns: (records, updated_offsets)
    """

    # Try unified JSON feed first
    try:
        feed = _read_unified_feed(chat_file)
    except Exception:
        feed = {}

    if feed:
        last_ts = int(offsets.get("feed_last_ts", 0) or 0)
        recent = offsets.get("feed_recent_fps") or []
        if not isinstance(recent, list):
            recent = []
        recent = [str(x) for x in recent][-500:]
        recent_set = set(recent)

        msgs = feed.get("messages") or []
        out: List[Dict[str, Any]] = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            ts = int(m.get("ts", 0) or 0)
            if ts <= 0:
                continue
            fp = _fingerprint_msg(m)
            if ts > last_ts or (ts == last_ts and fp not in recent_set):
                out.append(m)

        out.sort(key=lambda r: int(r.get("ts", 0) or 0))
        if out:
            last_ts = max(last_ts, max(int(r.get("ts", 0) or 0) for r in out))
            for r in out[-200:]:
                recent.append(_fingerprint_msg(r))
            recent = recent[-500:]

        offsets["feed_last_ts"] = last_ts
        offsets["feed_recent_fps"] = recent
        return out, offsets

    # Fall back to JSONL
    off = int(offsets.get("chat_feed_offset_bytes", 0) or 0)
    recs, off2 = read_new_jsonl(chat_file, off)
    if off2 != off:
        offsets["chat_feed_offset_bytes"] = off2
    return recs, offsets


def main() -> None:
    # services/ingestor.py -> ChatManager/
    base_dir = Path(__file__).resolve().parents[1]
    cfg = load_json(base_dir / "commands.txt", {})
    log = setup_logging("ingestor", cfg, base_dir)

    chat_file_raw = str(cfg.get("chat_file", "") or "").strip()
    chat_file_raw = expand_env(chat_file_raw)
    if not chat_file_raw:
        raise RuntimeError("commands.txt missing chat_file (or env var not set).")
    chat_file = resolve_from_bot_root(chat_file_raw)
    poll_ms = int(cfg.get("poll_ms", 350) or 350)
    process_existing = bool(cfg.get("process_existing_on_start", False))

    bus_dir = base_dir / "bus"
    bus_dir.mkdir(parents=True, exist_ok=True)
    events_out = bus_dir / "events.inbox.jsonl"
    ensure_file(events_out)

    state_dir = base_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    offsets_path = state_dir / "offsets.ingestor.json"
    offsets: Dict[str, Any] = load_json(offsets_path, {})

    ensure_file(chat_file)

    # Initialize offsets for both modes
    if "chat_feed_offset_bytes" not in offsets:
        try:
            offsets["chat_feed_offset_bytes"] = 0 if process_existing else chat_file.stat().st_size
        except Exception:
            offsets["chat_feed_offset_bytes"] = 0

    if "feed_last_ts" not in offsets:
        if process_existing:
            offsets["feed_last_ts"] = 0
        else:
            # If it's a unified feed, skip old messages by setting last_ts to current max ts.
            try:
                feed = _read_unified_feed(chat_file)
                msgs = feed.get("messages") or []
                offsets["feed_last_ts"] = max((int(m.get("ts", 0) or 0) for m in msgs if isinstance(m, dict)), default=0)
            except Exception:
                offsets["feed_last_ts"] = 0
        offsets["feed_recent_fps"] = []

    atomic_write_json(offsets_path, offsets)

    log.info("Started")
    log.info("chat_file=%s", str(chat_file))
    log.info("events_out=%s", str(events_out))

    while True:
        try:
            recs, offsets = read_new_records(chat_file, offsets)
            if recs:
                atomic_write_json(offsets_path, offsets)

            now = int(time.time())
            for r in recs:
                if not isinstance(r, dict):
                    continue
                user = r.get("user") or {}
                if not isinstance(user, dict):
                    user = {}
                if user.get("isBot") is True:
                    continue

                platform = str(r.get("platform", r.get("source", "")) or "").strip().lower() or "unknown"
                rtype = str(r.get("type", "chat") or "chat").lower()
                event = str(r.get("event", "") or "").lower()

                text = r.get("message", r.get("text", ""))

                out = {
                    "type": "chat" if rtype == "chat" else rtype,
                    "ts": int(r.get("ts", now) or now),
                    "platform": platform,
                    "user_key": stable_user_key(platform, user),
                    "reply_name": choose_reply_name(user),
                    "tier": detect_user_tier(user),
                    "text": text if isinstance(text, str) else "",
                    "event": event,
                }

                if rtype == "chat":
                    append_jsonl(events_out, out)
                elif out["type"] in ("like", "share"):
                    append_jsonl(events_out, out)

            time.sleep(max(0.05, poll_ms / 1000.0))
        except Exception:
            log.exception("Loop error")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
