import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import websockets
from dotenv import load_dotenv


def env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip())
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name, str(default)) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def now_ms() -> int:
    return int(time.time() * 1000)


def clamp_list(items: List[Any], max_len: int) -> List[Any]:
    if max_len <= 0:
        return []
    if len(items) <= max_len:
        return items
    return items[-max_len:]


def atomic_write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)  # atomic on Windows


def pick_platform(msg: Dict[str, Any]) -> str:
    # SSN commonly uses "type" as platform name; some messages use originalPlatform/platform.
    for k in ("originalPlatform", "platform", "type"):
        v = msg.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return "unknown"


def pick_string(msg: Dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        v = msg.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def any_truthy(msg: Dict[str, Any], *keys: str) -> bool:
    for k in keys:
        v = msg.get(k)
        if isinstance(v, bool):
            if v:
                return True
        elif isinstance(v, (int, float)):
            if v != 0:
                return True
        elif isinstance(v, str):
            if v.strip().lower() in ("1", "true", "yes", "on"):
                return True
    return False


def parse_badges(msg: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    SSN often provides badges as:
      - chatbadges: string
      - badges: array/objects (varies)
    We'll normalize to: [{name, url}]
    """
    out: List[Dict[str, str]] = []
    seen = set()

    def add_badge(name: str, url: str = ""):
        name = (name or "").strip()
        url = (url or "").strip()
        if not name:
            return
        key = f"{name.lower()}|{url}"
        if key in seen:
            return
        seen.add(key)
        out.append({"name": name, "url": url})

    # string badges
    s = pick_string(msg, "chatbadges", "chatBadges", default="")
    if s:
        for part in s.replace(",", " ").split():
            add_badge(part)

    # array/object badges
    b = msg.get("badges") or msg.get("userBadges")
    if isinstance(b, list):
        for item in b:
            if isinstance(item, str):
                add_badge(item)
            elif isinstance(item, dict):
                name = pick_string(item, "name", "title", "type", default="BADGE")
                url = pick_string(item, "url", "image", "img", "icon", "src", default="")
                add_badge(name, url)
    elif isinstance(b, dict):
        name = pick_string(b, "name", "title", "type", default="BADGE")
        url = pick_string(b, "url", "image", "img", "icon", "src", default="")
        add_badge(name, url)

    return out


@dataclass
class FeedState:
    max_messages: int
    active_window_seconds: int
    history: List[Dict[str, Any]] = field(default_factory=list)
    active_map: Dict[str, Dict[str, int]] = field(default_factory=dict)  # platform -> userKey -> lastSeenMs

    def prune_inactive(self, cutoff_ms: int) -> None:
        to_del_platforms = []
        for plat, users in self.active_map.items():
            stale = [uk for uk, last in users.items() if last < cutoff_ms]
            for uk in stale:
                users.pop(uk, None)
            if not users:
                to_del_platforms.append(plat)
        for plat in to_del_platforms:
            self.active_map.pop(plat, None)

    def active_counts(self) -> Tuple[int, Dict[str, int]]:
        by = {plat: len(users) for plat, users in self.active_map.items()}
        total = sum(by.values())
        return total, by

    def add_message(self, normalized: Dict[str, Any]) -> None:
        self.history.append(normalized)
        self.history = clamp_list(self.history, self.max_messages)


def normalize_ssn_message(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # We only care about actual chat-ish messages (they usually have chatname/chatmessage)
    username = pick_string(raw, "chatname", default="")
    message = pick_string(raw, "chatmessage", default="")
    if not username and not message:
        return None

    platform = pick_platform(raw)
    source = pick_string(raw, "source", default="ssn")
    user_id = pick_string(raw, "userid", "id", default="")
    avatar = pick_string(raw, "chatimg", "avatar", default="")

    # Role flags (best-effort; varies by platform)
    is_bot = any_truthy(raw, "isBot", "bot")
    is_mod = any_truthy(raw, "isMod", "ismod", "isModerator", "moderator", "mod")
    is_vip = any_truthy(raw, "isVip", "isVIP", "vip")
    is_sub = any_truthy(raw, "isSubscriber", "issubscriber", "isSub", "sub", "subscriber") or bool(pick_string(raw, "membership", default=""))
    is_broadcaster = any_truthy(raw, "isBroadcaster", "broadcaster", "owner")

    badges = parse_badges(raw)

    # Add role badges even if not present
    def ensure_badge(name: str):
        if any(b.get("name", "").lower() == name.lower() for b in badges):
            return
        badges.insert(0, {"name": name, "url": ""})

    if is_broadcaster:
        ensure_badge("BROADCASTER")
    if is_mod:
        ensure_badge("MOD")
    if is_vip:
        ensure_badge("VIP")
    if is_sub:
        ensure_badge("SUB")

    ts = now_ms()
    user_key = f"{platform}:{(user_id if user_id else username)}"

    return {
        "ts": ts,
        "platform": platform,
        "source": source,
        "message": message,
        "user": {
            "name": username or "Unknown",
            "id": user_id,
            "key": user_key,
            "avatar": avatar,
            "isBot": is_bot,
            "isMod": is_mod,
            "isVip": is_vip,
            "isSubscriber": is_sub,
            "isBroadcaster": is_broadcaster,
            "badges": badges,
        },
        # Keep some useful raw fields if you want in overlay later
        "rawType": raw.get("type", ""),
        "hasDonation": raw.get("hasDonation", ""),
    }


async def run_writer():
    load_dotenv()

    session = (os.getenv("SSN_SESSION") or "").strip()
    if not session:
        raise RuntimeError("SSN_SESSION missing in .env")

    out_path = (os.getenv("CHAT_FEED_PATH") or "").strip()
    if not out_path:
        raise RuntimeError("CHAT_FEED_PATH missing in .env")

    max_messages = env_int("MAX_MESSAGES", 200)
    active_window_seconds = env_int("ACTIVE_WINDOW_SECONDS", 300)
    reconnect_base = env_int("RECONNECT_BASE_SECONDS", 2)
    reconnect_max = env_int("RECONNECT_MAX_SECONDS", 20)
    log_every = env_bool("LOG_EVERY_MESSAGE", True)

    # Channel 4 receives chat messages when SSN toggles are enabled.
    # Docs: wss://io.socialstream.ninja/join/{SESSION}/4
    uri = f"wss://io.socialstream.ninja/join/{session}/4"

    state = FeedState(max_messages=max_messages, active_window_seconds=active_window_seconds)

    backoff = reconnect_base
    print(f"[SSNWriter] Connecting: {uri}")
    print(f"[SSNWriter] Writing to: {out_path}")

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
                print("[SSNWriter] Connected. Listening for chat...")

                backoff = reconnect_base

                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        if not isinstance(data, dict):
                            continue
                    except Exception:
                        continue

                    normalized = normalize_ssn_message(data)
                    if not normalized:
                        continue

                    plat = normalized["platform"]
                    user_key = normalized["user"]["key"]
                    ts = int(normalized["ts"])

                    # update active map + prune
                    if plat not in state.active_map:
                        state.active_map[plat] = {}
                    state.active_map[plat][user_key] = ts

                    cutoff = ts - (state.active_window_seconds * 1000)
                    state.prune_inactive(cutoff)

                    # history
                    state.add_message(normalized)

                    total_active, by_plat = state.active_counts()

                    output = {
                        "updatedTs": ts,
                        "activeWindowSeconds": state.active_window_seconds,
                        "activeTotal": total_active,
                        "activeByPlatform": by_plat,
                        "messages": state.history,
                    }

                    atomic_write_json(out_path, output)

                    if log_every:
                        u = normalized["user"]["name"]
                        m = normalized["message"]
                        print(f"[{plat}] {u}: {m}")

        except Exception as e:
            print(f"[SSNWriter] Disconnected/error: {type(e).__name__}: {e}")

        print(f"[SSNWriter] Reconnecting in {backoff}s...")
        await asyncio.sleep(backoff)
        backoff = min(reconnect_max, backoff * 2)


if __name__ == "__main__":
    asyncio.run(run_writer())

    print("[SSNWriter] Done.")