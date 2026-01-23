import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

# When this script is executed directly (python services/emitter.py), Python's
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
                items.append(json.loads(s))
            except Exception:
                continue
    return items, offset_bytes


def is_overlay_only(platform: str, prefixes: List[str]) -> bool:
    p = (platform or "").lower()
    for pref in prefixes:
        if p.startswith(str(pref).lower()):
            return True
    return False


def ssn_send(ssn_session: str, platform_map: Dict[str, str], platform: str, text: str) -> bool:
    if not ssn_session or ssn_session == "PUT_YOUR_SSN_SESSION_HERE":
        return False
    target = str(platform_map.get(platform, "null") or "null").strip() or "null"
    msg_enc = urllib.parse.quote(text, safe="")
    url = f"https://io.socialstream.ninja/{ssn_session}/sendEncodedChat/{target}/{msg_enc}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            code = getattr(resp, "status", 200)
            return 200 <= int(code) < 300
    except Exception:
        return False


def bot_prefix(bot: str, spotify_prefix: str) -> str:
    b = (bot or "").lower()
    if b == "spotify" and spotify_prefix:
        return spotify_prefix
    if b == "gamble":
        return "[Slots]"
    if b == "manager":
        return "[Manager]"
    if b:
        return f"[{b.capitalize()}Bot]"
    return ""


def clamp(s: str, n: int) -> str:
    s = s or ""
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _normalize_overlay_chat_path(p: Path) -> Path:
    # Prevent accidental writes into the SSN JSON feed file.
    if p.suffix.lower() == ".json":
        return p.with_name("overlay_additions.jsonl")
    return p


def trim_jsonl(p: Path, max_lines: int) -> None:
    if max_lines <= 0:
        return
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > max_lines + 50:
            p.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    cfg = load_json(base_dir / "commands.txt", {})
    log = setup_logging("emitter", cfg, base_dir)

    poll_ms = int(cfg.get("poll_ms", 350) or 350)

    reply_cfg = cfg.get("reply") or {}
    spotify_prefix = str(reply_cfg.get("prefix", "") or "").strip()
    max_len = int(reply_cfg.get("max_len", 240) or 240)

    ssn_cfg = cfg.get("ssn") or {}
    ssn_enabled = bool(ssn_cfg.get("enabled", False))
    ssn_session = str(ssn_cfg.get("session", "") or "").strip()
    ssn_session = expand_env(ssn_session)
    platform_map = ssn_cfg.get("platform_map") or {}

    policy = cfg.get("reply_policy") or {}
    overlay_only_prefixes = policy.get("overlay_only_platform_prefixes") or ["tiktok"]

    overlay_cfg = cfg.get("overlay_fallback") or {}
    overlay_enabled = bool(overlay_cfg.get("enabled", True))

    overlay_chat_file_raw = str(overlay_cfg.get("chat_file", "") or "").strip() or ""
    overlay_events_file_raw = str(overlay_cfg.get("overlay_events_file", "") or "").strip() or ""
    overlay_chat_file_raw = expand_env(overlay_chat_file_raw)
    overlay_events_file_raw = expand_env(overlay_events_file_raw)

    overlay_max = int(overlay_cfg.get("max_messages", 400) or 400)
    overlay_events_max = int(overlay_cfg.get("max_events", overlay_max) or overlay_max)

    overlay_chat_file = resolve_from_bot_root(overlay_chat_file_raw) if overlay_chat_file_raw else None
    overlay_events_file = resolve_from_bot_root(overlay_events_file_raw) if overlay_events_file_raw else None

    if overlay_chat_file is not None:
        overlay_chat_file = _normalize_overlay_chat_path(overlay_chat_file)
        ensure_file(overlay_chat_file)

    if overlay_events_file is not None:
        overlay_events_file = _normalize_overlay_chat_path(overlay_events_file)
        ensure_file(overlay_events_file)

    bus_dir = base_dir / "bus"
    replies_in = bus_dir / "replies.outbox.jsonl"
    overlay_in = bus_dir / "overlay.outbox.jsonl"
    ensure_file(replies_in)
    ensure_file(overlay_in)

    state_dir = base_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    offsets_path = state_dir / "offsets.emitter.json"
    offsets = load_json(offsets_path, {"replies_offset_bytes": 0, "overlay_offset_bytes": 0})

    log.info("Started")
    log.info("replies_in=%s", str(replies_in))
    log.info("overlay_in=%s", str(overlay_in))
    if overlay_enabled:
        log.info("overlay_chat_file=%s", str(overlay_chat_file) if overlay_chat_file else "(disabled)")
        log.info("overlay_events_file=%s", str(overlay_events_file) if overlay_events_file else "(disabled)")

    while True:
        try:
            # 1) Overlay events -> overlay events file (JSONL)
            ooff = int(offsets.get("overlay_offset_bytes", 0) or 0)
            orec, ooff2 = read_new_jsonl(overlay_in, ooff)
            if ooff2 != ooff:
                offsets["overlay_offset_bytes"] = ooff2
                atomic_write_json(offsets_path, offsets)

            if orec and overlay_enabled and overlay_events_file is not None:
                for ev in orec:
                    if not isinstance(ev, dict):
                        continue
                    append_jsonl(
                        overlay_events_file,
                        {
                            "type": "overlay_event",
                            "ts": int(ev.get("ts", time.time()) or time.time()),
                            "overlay": ev.get("overlay", ""),
                            "event": ev.get("event", ""),
                            "event_id": ev.get("event_id", ""),
                            "payload": ev.get("payload", {}) or {},
                            "user": {"isBot": True, "name": "SYSTEM", "key": "bot:system"},
                        },
                    )
                trim_jsonl(overlay_events_file, overlay_events_max)

            # 2) Reply intents -> SSN (if allowed) else overlay chat additions file (JSONL)
            roff = int(offsets.get("replies_offset_bytes", 0) or 0)
            rrec, roff2 = read_new_jsonl(replies_in, roff)
            if roff2 != roff:
                offsets["replies_offset_bytes"] = roff2
                atomic_write_json(offsets_path, offsets)

            for r in rrec:
                if not isinstance(r, dict):
                    continue
                if r.get("type") != "reply_intent":
                    continue

                platform = str(r.get("platform", "unknown") or "unknown").lower()
                reply_name = str(r.get("reply_name", "") or "User")
                text = str(r.get("text", "") or "")
                bot = str(r.get("bot", "") or "")

                prefix = bot_prefix(bot, spotify_prefix)
                msg = f"@{reply_name} {text}".strip()
                if prefix:
                    msg = f"{prefix} {msg}"
                msg = clamp(msg, max_len)

                # TikTok (or other configured platforms): overlay-only
                if is_overlay_only(platform, overlay_only_prefixes):
                    if overlay_enabled and overlay_chat_file is not None:
                        append_jsonl(
                            overlay_chat_file,
                            {
                                "type": "chat",
                                "ts": int(time.time()),
                                "platform": platform,
                                "message": msg,
                                "user": {"isBot": True, "name": "ChatManager", "key": "bot:chatmanager"},
                                "source": "chatmanager",
                            },
                        )
                        trim_jsonl(overlay_chat_file, overlay_max)
                    continue

                sent = False
                if ssn_enabled:
                    sent = ssn_send(ssn_session, platform_map, platform, msg)

                if (not sent) and overlay_enabled and overlay_chat_file is not None:
                    append_jsonl(
                        overlay_chat_file,
                        {
                            "type": "chat",
                            "ts": int(time.time()),
                            "platform": platform,
                            "message": msg,
                            "user": {"isBot": True, "name": "ChatManager", "key": "bot:chatmanager"},
                            "source": "chatmanager",
                        },
                    )
                    trim_jsonl(overlay_chat_file, overlay_max)

            time.sleep(max(0.05, poll_ms / 1000.0))
        except Exception:
            log.exception("Loop error")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
