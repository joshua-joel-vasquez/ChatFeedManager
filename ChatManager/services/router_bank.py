import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# When this script is executed directly (python services/router_bank.py), Python's
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


# ---------------- file helpers ----------------
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


# ---------------- tiers ----------------
TIER_ORDER = ["EVERYONE", "SUB", "VIP", "MOD", "BROADCASTER"]
TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}


def tier_ge(a: str, b: str) -> bool:
    return TIER_RANK.get(a, 0) >= TIER_RANK.get(b, 0)


# ---------------- gamble FIFO queue ----------------
@dataclass
class GambleTask:
    task_id: str
    action: str
    bet: int
    platform: str
    reply_name: str
    user_key: str
    created_ts: int
    available_points: int
    slots_cfg: Optional[Dict[str, Any]] = None
    command: str = "slots"


class GambleQueue:
    def __init__(self, path: Path):
        self.path = path
        self.data = load_json(self.path, {"queue": [], "active": None, "busy_until_ts": 0})

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    def reserved_points_for_user(self, user_key: str) -> int:
        total = 0
        for t in self.data.get("queue", []):
            if t.get("user_key") == user_key:
                total += int(t.get("bet", 0) or 0)
        a = self.data.get("active")
        if a and a.get("user_key") == user_key:
            total += int(a.get("bet", 0) or 0)
        return total

    def enqueue(self, task: GambleTask) -> int:
        self.data.setdefault("queue", []).append(asdict(task))
        self.save()
        return len(self.data["queue"])

    def active_task_id(self) -> Optional[str]:
        a = self.data.get("active")
        return a.get("task_id") if a else None

    def can_dispatch(self, now_ts: int) -> bool:
        if self.data.get("active") is not None:
            return False
        if now_ts < int(self.data.get("busy_until_ts", 0) or 0):
            return False
        return len(self.data.get("queue", [])) > 0

    def pop_next_for_dispatch(self) -> Optional[Dict[str, Any]]:
        q = self.data.get("queue", [])
        if not q:
            return None
        nxt = q.pop(0)
        self.data["active"] = nxt
        self.save()
        return nxt

    def mark_done(self, blocking_ms: int = 0) -> None:
        self.data["active"] = None
        if blocking_ms and blocking_ms > 0:
            self.data["busy_until_ts"] = int(time.time()) + max(0, int(blocking_ms)) // 1000
        else:
            self.data["busy_until_ts"] = int(time.time())
        self.save()



# ---------------- slots config ----------------
# Customize slots by editing: ChatManager/config/slots_config.json
# - Change multipliers by editing `payouts[*].mult`
# - Add combinations by adding new `payouts` entries (order matters; first match wins)
# Pattern supports '*' wildcard. Example: ["7","7","*"] means any third symbol.

DEFAULT_RESULTCODE_MULT = {
    "SLOTS_777": 25,
    "SLOTS_TRIPLE_BAR": 15,
    "SLOTS_TRIPLE_CHERRY": 8,
    "SLOTS_DOUBLE_7": 3,
    "SLOTS_DOUBLE_CHERRY": 2,
    "SLOTS_SINGLE_CHERRY": 1,
    "SLOTS_LOSS": 0,
}

DEFAULT_RESULTCODE_SYMBOLS = {
    "SLOTS_777": ["7", "7", "7"],
    "SLOTS_TRIPLE_BAR": ["BAR", "BAR", "BAR"],
    "SLOTS_TRIPLE_CHERRY": ["🍒", "🍒", "🍒"],
    "SLOTS_DOUBLE_7": ["7", "7", "*"],
    "SLOTS_DOUBLE_CHERRY": ["🍒", "🍒", "*"],
    "SLOTS_SINGLE_CHERRY": ["🍒", "*", "*"],
    "SLOTS_LOSS": ["?", "?", "?"],
}

DEFAULT_SLOTS_CONFIG = {
    "reels": ["🍒", "🍋", "🍇", "🔔", "⭐", "BAR", "7"],
    "payouts": [
        {"name": "777", "pattern": ["7", "7", "7"], "mult": 25, "result_code": "SLOTS_777"},
        {"name": "TRIPLE_BAR", "pattern": ["BAR", "BAR", "BAR"], "mult": 15, "result_code": "SLOTS_TRIPLE_BAR"},
        {"name": "TRIPLE_CHERRY", "pattern": ["🍒", "🍒", "🍒"], "mult": 8, "result_code": "SLOTS_TRIPLE_CHERRY"},
        {"name": "DOUBLE_7", "pattern": ["7", "7", "*"], "mult": 3, "result_code": "SLOTS_DOUBLE_7"},
        {"name": "DOUBLE_CHERRY", "pattern": ["🍒", "🍒", "*"], "mult": 2, "result_code": "SLOTS_DOUBLE_CHERRY"},
        {"name": "SINGLE_CHERRY", "pattern": ["🍒", "*", "*"], "mult": 1, "result_code": "SLOTS_SINGLE_CHERRY"},
    ],
    "default_loss_mult": 0,
}


def _slots_pattern_match(pattern, symbols) -> bool:
    try:
        if not isinstance(pattern, list) or not isinstance(symbols, list):
            return False
        if len(pattern) != len(symbols):
            return False
        for p, s in zip(pattern, symbols):
            ps = str(p)
            if ps in ("*", "ANY", "any", ""):
                continue
            if ps != str(s):
                return False
        return True
    except Exception:
        return False


def _normalize_slots_cfg(cfg):
    # Merge user cfg onto defaults, and normalize types.
    out = dict(DEFAULT_SLOTS_CONFIG)
    if isinstance(cfg, dict):
        for k in ("reels", "payouts", "default_loss_mult"):
            if k in cfg:
                out[k] = cfg.get(k)

    reels = out.get("reels")
    if not isinstance(reels, list) or not reels:
        reels = list(DEFAULT_SLOTS_CONFIG["reels"])
    out["reels"] = [str(x) for x in reels]

    payouts = out.get("payouts")
    if not isinstance(payouts, list) or not payouts:
        payouts = list(DEFAULT_SLOTS_CONFIG["payouts"])

    norm = []
    for r in payouts:
        if not isinstance(r, dict):
            continue
        pat = r.get("pattern", r.get("symbols"))
        if not isinstance(pat, list) or len(pat) != 3:
            continue
        mult = r.get("mult", r.get("multiplier", 0))
        try:
            mult = int(mult)
        except Exception:
            mult = 0
        name = str(r.get("name", "") or "").strip() or "PAYOUT"
        result_code = str(r.get("result_code", "") or "").strip()
        norm.append({"name": name, "pattern": [str(x) for x in pat], "mult": mult, "result_code": result_code})

    if not norm:
        norm = list(DEFAULT_SLOTS_CONFIG["payouts"])

    out["payouts"] = norm

    try:
        out["default_loss_mult"] = int(out.get("default_loss_mult", 0) or 0)
    except Exception:
        out["default_loss_mult"] = 0

    return out


def load_slots_config(path: Path) -> dict:
    # Auto-create a default config file if it doesn't exist.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(json.dumps(DEFAULT_SLOTS_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
            return _normalize_slots_cfg(DEFAULT_SLOTS_CONFIG)
        cfg = load_json(path, DEFAULT_SLOTS_CONFIG)
        return _normalize_slots_cfg(cfg)
    except Exception:
        return _normalize_slots_cfg(DEFAULT_SLOTS_CONFIG)


def _coerce_symbols(v) -> list:
    if isinstance(v, list):
        return [str(x) for x in v][:3]
    if isinstance(v, str):
        s = v.strip()
        if "|" in s:
            parts = [p.strip() for p in s.split("|") if p.strip()]
            return [str(x) for x in parts][:3]
        if "," in s:
            parts = [p.strip() for p in s.split(",") if p.strip()]
            return [str(x) for x in parts][:3]
        parts = [p.strip() for p in s.split() if p.strip()]
        return [str(x) for x in parts][:3]
    return []


def eval_slots(symbols: list, result_code: str, cfg: dict) -> tuple:
    """Return (mult, rule_name, resolved_result_code, resolved_symbols)."""
    syms = [str(x) for x in (symbols or [])][:3]

    # If we have no symbols, try a back-compat mapping.
    if (not syms or len(syms) != 3) and result_code:
        mult = int(DEFAULT_RESULTCODE_MULT.get(result_code, 0))
        mapped = list(DEFAULT_RESULTCODE_SYMBOLS.get(result_code, ["?", "?", "?"]))
        return mult, result_code, result_code, mapped

    while len(syms) < 3:
        syms.append("?")

    for r in (cfg.get("payouts") or []):
        pat = r.get("pattern")
        if _slots_pattern_match(pat, syms):
            mult = int(r.get("mult", 0) or 0)
            name = str(r.get("name", "") or "").strip() or "WIN"
            rc = str(r.get("result_code", "") or "").strip() or (result_code or "")
            return mult, name, rc, syms

    if result_code and result_code in DEFAULT_RESULTCODE_MULT:
        mapped = list(DEFAULT_RESULTCODE_SYMBOLS.get(result_code, ["?", "?", "?"]))
        return int(DEFAULT_RESULTCODE_MULT.get(result_code, 0)), result_code, result_code, mapped

    return int(cfg.get("default_loss_mult", 0) or 0), "LOSS", (result_code or "SLOTS_LOSS"), syms




# ---------------- router/bank ----------------
class RouterBank:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.cfg = load_json(self.base_dir / "commands.txt", {})

        self.log = setup_logging('router_bank', self.cfg, self.base_dir)

        # core
        self.poll_ms = int(self.cfg.get("poll_ms", 350) or 350)

        # state
        state_cfg = self.cfg.get("state") or {}
        self.user_state_path = self._resolve_relative(state_cfg.get("user_state_file", "state/user_state.json"))
        self.inflight_path = self._resolve_relative(state_cfg.get("inflight_file", "state/inflight.json"))

        self.user_state: Dict[str, Any] = load_json(self.user_state_path, {})
        self.inflight: Dict[str, Any] = load_json(self.inflight_path, {})

        # points ledger (command receipts / auditing)
        self.ledger_path = self.base_dir / 'state' / 'points_ledger.jsonl'
        ensure_file(self.ledger_path)

        # slots config (customizable reels/payouts)
        self.slots_cfg_path = self.base_dir / 'config' / 'slots_config.json'
        self._slots_cfg_mtime = 0.0
        self.slots_cfg = load_slots_config(self.slots_cfg_path)
        try:
            self._slots_cfg_mtime = self.slots_cfg_path.stat().st_mtime
        except Exception:
            self._slots_cfg_mtime = 0.0

        # earning
        earning = self.cfg.get("earning") or {}
        self.active_window_seconds = int(earning.get("active_window_seconds", 300) or 300)
        self.points_per_minute_active = int(earning.get("points_per_minute_active", 1) or 1)
        self.points_per_message = int(earning.get("points_per_message", 2) or 2)
        self.points_per_like = int(earning.get("points_per_like", 1) or 1)
        self.points_per_share = int(earning.get("points_per_share", 5) or 5)
        self._last_active_award_ts = int(time.time())

        # buses
        self.bus_dir = self.base_dir / "bus"
        self.bus_dir.mkdir(parents=True, exist_ok=True)
        self.events_in = self.bus_dir / "events.inbox.jsonl"
        self.replies_out = self.bus_dir / "replies.outbox.jsonl"
        self.overlay_out = self.bus_dir / "overlay.outbox.jsonl"
        ensure_file(self.events_in)
        ensure_file(self.replies_out)
        ensure_file(self.overlay_out)

        # Optional: mirror points state into the overlay folder so overlays can fetch same-origin
        overlay_cfg = self.cfg.get('overlay_fallback') or {}
        mirror_path_raw = overlay_cfg.get('user_state_mirror_file') or ''
        mirror_path_raw = expand_env(mirror_path_raw)
        self.user_state_mirror_path = None
        if mirror_path_raw:
            try:
                self.user_state_mirror_path = resolve_from_bot_root(mirror_path_raw)
                # Ensure it exists early
                atomic_write_json(self.user_state_mirror_path, self.user_state)
            except Exception:
                self.log.exception('Failed to initialize user_state mirror')

        # offsets
        self.offsets_path = self.base_dir / "state" / "offsets.router.json"
        self.offsets = load_json(self.offsets_path, {
            "events_in_offset_bytes": 0,
            "bot_offsets": {}
        })

        # bots config
        self.bots = self._parse_bots(self.cfg.get("bots", []) or [])
        for bot_id in self.bots.keys():
            self.offsets["bot_offsets"].setdefault(bot_id, {"outbox_offset_bytes": 0, "ack_offset_bytes": 0})

        # commands config
        self.manager_commands = self._index_cmds(self.cfg.get("manager_commands", []) or [])
        self.commands = self._index_cmds(self.cfg.get("commands", []) or [])
        self.help_header_lines = ((self.cfg.get("help") or {}).get("header_lines")) or [
            'Every command starts with "!" and must be at the beginning of your message.',
            "Commands are case-insensitive.",
        ]

        # gamble queue
        self.gamble_queue = GambleQueue(self.base_dir / "state" / "gamble_queue.json")

        self._dirty_user_state = False
        self._dirty_inflight = False
        self._dirty_offsets = False

        # Defensive deduplication for chat commands.
        # In rare cases the upstream feed can briefly surface the same message
        # twice (e.g., key normalization changes, partial rewrites). Cooldowns
        # should prevent most duplicates, but user_key mismatches can bypass
        # that. We keep a tiny sliding window to avoid dispatching duplicate
        # tasks (and therefore duplicate Spotify API calls).
        self._recent_cmd_exact: Dict[str, float] = {}
        self._recent_cmd_loose: Dict[str, float] = {}
        self._recent_cmd_window_exact_sec = 15.0
        self._recent_cmd_window_loose_sec = 2.0

    def _resolve_relative(self, p: Any) -> Path:
        s = str(p or "").replace("\\", "/").strip()
        if not s:
            return (self.base_dir / "state" / "missing.json").resolve()
        path = Path(s)
        if path.is_absolute():
            return path
        if s.lower().startswith("chatmanager/"):
            s = s.split("/", 1)[1]
            path = Path(s)
        return (self.base_dir / path).resolve()

    def _parse_bots(self, bots_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Path]]:
        bots: Dict[str, Dict[str, Path]] = {}
        for b in bots_list:
            if not isinstance(b, dict):
                continue
            if b.get("enabled", True) is False:
                continue
            bot_id = str(b.get("id", "")).strip().lower()
            if not bot_id:
                continue
            inbox = self._resolve_relative(b.get("inbox", f"bus/{bot_id}.inbox.jsonl"))
            outbox = self._resolve_relative(b.get("outbox", f"bus/{bot_id}.outbox.jsonl"))
            ack = self._resolve_relative(b.get("ack", f"bus/{bot_id}.ack.jsonl"))
            deadletter = self._resolve_relative(b.get("deadletter", f"bus/deadletter.{bot_id}.jsonl"))
            ensure_file(inbox); ensure_file(outbox); ensure_file(ack); ensure_file(deadletter)
            bots[bot_id] = {"inbox": inbox, "outbox": outbox, "ack": ack, "deadletter": deadletter}
        return bots

    def _index_cmds(self, cmd_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        idx: Dict[str, Dict[str, Any]] = {}
        for c in cmd_list:
            if not isinstance(c, dict):
                continue
            name = str(c.get("command", "")).strip().lower()
            if not name:
                continue
            c = dict(c)
            c["command"] = name
            c["aliases"] = [str(a).strip().lower() for a in (c.get("aliases") or []) if str(a).strip()]
            c["bot"] = str(c.get("bot", "")).strip().lower()
            c["action"] = str(c.get("action", "")).strip().lower()
            idx[name] = c
            for a in c["aliases"]:
                idx[a] = c
        return idx

    # ---------- state ----------
    def _get_user_rec(self, user_key: str) -> Dict[str, Any]:
        rec = self.user_state.get(user_key)
        if not isinstance(rec, dict):
            rec = {}
            self.user_state[user_key] = rec
            self._dirty_user_state = True
        rec.setdefault("points", 0)
        rec.setdefault("last_seen_ts", 0)
        rec.setdefault("last_award_ts", int(time.time()))
        rec.setdefault("cooldowns", {})
        return rec

    def get_points(self, user_key: str) -> int:
        return int(self._get_user_rec(user_key).get("points", 0) or 0)

    def set_points(self, user_key: str, pts: int) -> None:
        self._get_user_rec(user_key)["points"] = max(0, int(pts))
        self._dirty_user_state = True

    def add_points(self, user_key: str, delta: int) -> None:
        self.set_points(user_key, self.get_points(user_key) + int(delta))

    # ---------- cooldowns ----------
    def _cooldown_ok(self, user_key: str, cmd_name: str, cooldown_seconds: int, bypass_tier: str, user_tier: str) -> bool:
        if cooldown_seconds <= 0:
            return True
        if bypass_tier and tier_ge(user_tier, bypass_tier):
            return True
        cds = self._get_user_rec(user_key).get("cooldowns") or {}
        last = int(cds.get(cmd_name, 0) or 0)
        return (int(time.time()) - last) >= cooldown_seconds

    def _cooldown_remaining(self, user_key: str, cmd_name: str, cooldown_seconds: int) -> int:
        if cooldown_seconds <= 0:
            return 0
        try:
            cds = self._get_user_rec(user_key).get('cooldowns') or {}
            last = int(cds.get(cmd_name, 0) or 0)
            rem = int(cooldown_seconds) - (int(time.time()) - last)
            return max(0, rem)
        except Exception:
            return 0

    def _set_cooldown(self, user_key: str, cmd_name: str) -> None:
        rec = self._get_user_rec(user_key)
        cds = rec.get("cooldowns")
        if not isinstance(cds, dict):
            cds = {}
            rec["cooldowns"] = cds
        cds[cmd_name] = int(time.time())
        self._dirty_user_state = True

    # ---------- output helpers ----------
    def emit_reply(self, platform: str, reply_name: str, text: str, bot_id: str) -> None:
        append_jsonl(self.replies_out, {
            "type": "reply_intent",
            "ts": int(time.time()),
            "platform": platform,
            "reply_name": reply_name,
            "text": str(text),
            "bot": bot_id,
        })

    def emit_overlay(self, overlay: str, event: str, payload: Dict[str, Any], event_id: str) -> None:
        append_jsonl(self.overlay_out, {
            "type": "overlay_event",
            "ts": int(time.time()),
            "overlay": overlay,
            "event": event,
            "event_id": event_id,
            "payload": payload or {},
        })
    def _refresh_slots_cfg(self) -> None:
        try:
            m = self.slots_cfg_path.stat().st_mtime
            if m != self._slots_cfg_mtime:
                self.slots_cfg = load_slots_config(self.slots_cfg_path)
                self._slots_cfg_mtime = m
        except Exception:
            pass


    def _plural_pts(self, n: int) -> str:
        return 'pt' if int(n) == 1 else 'pts'

    def record_ledger(self, user_key: str, platform: str, cmd_name: str, bot_id: str, delta: int, before: int, after: int, note: str = '') -> None:
        try:
            append_jsonl(self.ledger_path, {
                'ts': int(time.time()),
                'platform': str(platform or ''),
                'user_key': str(user_key or ''),
                'command': str(cmd_name or ''),
                'bot': str(bot_id or ''),
                'delta': int(delta),
                'before': int(before),
                'after': int(after),
                'note': str(note or ''),
            })
        except Exception:
            self.log.exception('Failed to write points ledger')

    def emit_command_receipt(self, platform: str, reply_name: str, cmd_name: str, cost: int, new_total: int, bot_id: str, note: str = '') -> None:
        cost = int(cost)
        new_total = int(new_total)
        msg = f"Receipt: !{cmd_name} cost {cost} {self._plural_pts(cost)}. New total: {new_total} {self._plural_pts(new_total)}."
        if note:
            msg = msg + ' ' + str(note).strip()
        self.emit_reply(platform, reply_name, msg, bot_id=bot_id)

    # ---------- commands ----------
    def parse_command(self, text: str) -> Optional[Tuple[str, str]]:
        if not isinstance(text, str):
            return None
        if not text.startswith("!"):
            return None
        raw = text[1:]
        if not raw:
            return None
        parts = raw.split(None, 1)
        cmd = parts[0].strip().lower()
        args = parts[1] if len(parts) > 1 else ""
        return cmd, args

    def _parse_bet(self, args: str, spendable: int) -> int:
        a = (args or "").strip().lower()
        if not a:
            return min(50, spendable) if spendable > 0 else 0
        if a in ("max", "all"):
            return spendable
        try:
            return max(0, int(a))
        except Exception:
            return 0
    def handle_manager_command(self, cdef: Dict[str, Any], platform: str, reply_name: str, user_key: str, user_tier: str) -> None:
        min_tier = str(cdef.get("min_tier", "EVERYONE") or "EVERYONE").upper()
        if not tier_ge(user_tier, min_tier):
            return

        cmd_name = cdef["command"]
        cd = int(cdef.get("cooldown_seconds", 0) or 0)
        bypass = str(cdef.get("cooldown_bypass_tier", "") or "").upper()
        cost = int(cdef.get("cost_points", 0) or 0)

        if not self._cooldown_ok(user_key, cmd_name, cd, bypass, user_tier):
            rem = self._cooldown_remaining(user_key, cmd_name, cd)
            if rem > 0:
                pts_now = self.get_points(user_key)
                self.emit_reply(platform, reply_name, f"!{cmd_name} is on cooldown for {rem}s.", bot_id="manager")
                self.emit_reply(
                    platform,
                    reply_name,
                    f"Receipt: !{cmd_name} cost {cost} pts (not charged - cooldown). Total: {pts_now} pts.",
                    bot_id="manager",
                )
            return
        self._set_cooldown(user_key, cmd_name)

        # Manager commands are free by default, but still emit a receipt for transparency.
        pts_now = self.get_points(user_key)

        if cmd_name == "points":
            # Combine the points response + receipt into one line to reduce spam.
            self.emit_reply(
                platform,
                reply_name,
                f"You have {pts_now} points. Receipt: !{cmd_name} cost {cost} pts. New total: {pts_now} pts.",
                bot_id="manager",
            )
        elif cmd_name == "spothelp":
            self.emit_command_receipt(platform, reply_name, cmd_name, cost, pts_now, bot_id="manager")
            self.send_help(platform, reply_name, user_key, user_tier)

    def send_help(self, platform: str, reply_name: str, user_key: str, user_tier: str) -> None:
        pts = self.get_points(user_key)
        lines: List[str] = []
        lines.extend(self.help_header_lines)
        lines.append("")

        def collect(cmap: Dict[str, Dict[str, Any]]) -> List[str]:
            out: List[str] = []
            seen = set()
            for k, c in cmap.items():
                if c.get("command") != k:
                    continue
                if not c.get("show_in_help", False):
                    continue
                name = c["command"]
                if name in seen:
                    continue
                seen.add(name)
                min_tier = str(c.get("min_tier", "EVERYONE") or "EVERYONE").upper()
                if not tier_ge(user_tier, min_tier):
                    continue
                cost = int(c.get("cost_points", 0) or 0)
                if cost > pts:
                    continue
                hl = c.get("help_lines") or []
                if isinstance(hl, list):
                    out.extend([str(x) for x in hl])
            return out

        mgr_lines = collect(self.manager_commands)
        cmd_lines = collect(self.commands)

        if mgr_lines:
            lines.append("Manager commands:")
            lines.extend(mgr_lines)
            lines.append("")
        if cmd_lines:
            lines.append("Bot commands:")
            lines.extend(cmd_lines)
            lines.append("")

        # chunk into multiple reply intents
        chunk = ""
        for ln in "\n".join(lines).strip().splitlines():
            add = ln + "\n"
            if len(chunk) + len(add) > 220:
                self.emit_reply(platform, reply_name, chunk.strip(), bot_id="manager")
                chunk = ""
            chunk += add
        if chunk.strip():
            self.emit_reply(platform, reply_name, chunk.strip(), bot_id="manager")
    def enqueue_gamble(self, cmd_def: Dict[str, Any], platform: str, reply_name: str, user_key: str, args: str) -> None:
        self._refresh_slots_cfg()
        points = self.get_points(user_key)
        reserved = self.gamble_queue.reserved_points_for_user(user_key)
        spendable = max(0, points - reserved)

        cmd_name = str(cmd_def.get('command', 'slots') or 'slots')
        bet = self._parse_bet(args, spendable)

        if bet <= 0:
            # Not charged. Still provide a receipt and show wager availability.
            self.emit_reply(platform, reply_name, f"You have {spendable} points available to wager.", bot_id='gamble')
            self.emit_reply(
                platform,
                reply_name,
                f"Receipt: !{cmd_name} cost 0 pts. New total: {points} pts. Available to wager: {spendable} pts.",
                bot_id='gamble',
            )
            return

        if bet > spendable:
            self.emit_reply(platform, reply_name, f"Max wager is {spendable}.", bot_id='gamble')
            self.emit_reply(
                platform,
                reply_name,
                f"Receipt: !{cmd_name} cost 0 pts. New total: {points} pts. Available to wager: {spendable} pts.",
                bot_id='gamble',
            )
            return

        task = GambleTask(
            task_id='g_' + uuid.uuid4().hex[:10],
            action=cmd_def.get('action', 'slots') or 'slots',
            bet=bet,
            platform=platform,
            reply_name=reply_name,
            user_key=user_key,
            created_ts=int(time.time()),
            available_points=spendable,
            slots_cfg=self.slots_cfg,
            command=cmd_name,
        )

        pos = self.gamble_queue.enqueue(task)
        available_after = max(0, points - (reserved + bet))

        self.emit_reply(platform, reply_name, f"You’re queued (# {pos}). Wager: {bet}.", bot_id='gamble')
        self.emit_reply(
            platform,
            reply_name,
            f"Receipt: !{cmd_name} cost {bet} pts (reserved wager). New total: {points} pts. Available to wager: {available_after} pts.",
            bot_id='gamble',
        )
        self.record_ledger(user_key, platform, cmd_name, 'gamble', 0, points, points, note=f'wager_reserved={bet}; available_after={available_after}')


    def dispatch_to_worker(self, bot_id: str, cmd_def: Dict[str, Any], platform: str, reply_name: str, user_key: str, user_tier: str, args: str) -> None:
        task_id = "t_" + uuid.uuid4().hex[:12]
        task = {
            "type": "task",
            "task_id": task_id,
            "ts": int(time.time()),
            "bot": bot_id,
            "action": cmd_def.get("action", ""),
            "command": cmd_def.get("command", ""),
            "args": args,
            "platform": platform,
            "reply_name": reply_name,
            "user_key": user_key,
            "user_tier": user_tier,
        }
        append_jsonl(self.bots[bot_id]["inbox"], task)
        self.inflight[task_id] = {
            "bot": bot_id,
            "platform": platform,
            "reply_name": reply_name,
            "user_key": user_key,
            "created_ts": int(time.time()),
        }
        self._dirty_inflight = True
    def handle_bot_command(self, cdef: Dict[str, Any], platform: str, reply_name: str, user_key: str, user_tier: str, args: str) -> None:
        min_tier = str(cdef.get("min_tier", "EVERYONE") or "EVERYONE").upper()
        if not tier_ge(user_tier, min_tier):
            return

        cmd_name = cdef["command"]
        bot_id = str(cdef.get("bot", "") or "").strip().lower() or "manager"

        cd = int(cdef.get("cooldown_seconds", 0) or 0)
        bypass = str(cdef.get("cooldown_bypass_tier", "") or "").upper()

        if not self._cooldown_ok(user_key, cmd_name, cd, bypass, user_tier):
            rem = self._cooldown_remaining(user_key, cmd_name, cd)
            if rem > 0:
                pts_now = self.get_points(user_key)
                # Gamble has dynamic wager sizing; show cost as 0 on cooldown.
                cost_static = 0 if bot_id == "gamble" else int(cdef.get("cost_points", 0) or 0)
                self.emit_reply(platform, reply_name, f"!{cmd_name} is on cooldown for {rem}s.", bot_id=bot_id)
                self.emit_reply(
                    platform,
                    reply_name,
                    f"Receipt: !{cmd_name} cost {cost_static} pts (not charged - cooldown). Total: {pts_now} pts.",
                    bot_id=bot_id,
                )
            return
        self._set_cooldown(user_key, cmd_name)

        # Special: gamble uses dynamic wager sizing (not static cost_points).
        if bot_id == "gamble":
            self.enqueue_gamble(cdef, platform, reply_name, user_key, args)
            return

        # Standard commands: deduct configured cost_points (if any) and emit a receipt.
        cost = int(cdef.get("cost_points", 0) or 0)
        pts_before = self.get_points(user_key)

        if cost > 0 and pts_before < cost:
            # Not charged — insufficient funds, but still return a clear receipt.
            self.emit_reply(
                platform,
                reply_name,
                f"You need {cost} points for that command. You have {pts_before}."
                + f" Receipt: !{cmd_name} cost {cost} pts (not charged). Total: {pts_before} pts.",
                bot_id=bot_id,
            )
            return

        pts_after = pts_before
        if cost > 0:
            pts_after = max(0, pts_before - cost)
            self.set_points(user_key, pts_after)
            self.record_ledger(user_key, platform, cmd_name, bot_id, -cost, pts_before, pts_after, note='command_cost')

        # Always emit a receipt for recognized commands.
        self.emit_command_receipt(platform, reply_name, cmd_name, cost, pts_after, bot_id=bot_id)

        if bot_id in self.bots:
            self.dispatch_to_worker(bot_id, cdef, platform, reply_name, user_key, user_tier, args)


    # ---------- worker replies ----------
    def handle_worker_reply(self, bot_id: str, rec: Dict[str, Any]) -> None:
        if rec.get("type") != "reply":
            return

        if bot_id == "gamble":
            self.handle_gamble_reply(rec)
            return

        task_id = str(rec.get("task_id", "") or "")
        meta = self.inflight.get(task_id)
        if not meta:
            append_jsonl(self.bots[bot_id]["deadletter"], {"type": "orphan_reply", "ts": int(time.time()), "record": rec})
            return

        platform = meta.get("platform", "")
        reply_name = meta.get("reply_name", "")
        for m in (rec.get("messages") or [])[:3]:
            self.emit_reply(platform, reply_name, str(m), bot_id=bot_id)

        self.inflight.pop(task_id, None)
        self._dirty_inflight = True

    def handle_gamble_reply(self, rec: Dict[str, Any]) -> None:
        active_id = self.gamble_queue.active_task_id()
        if not active_id or str(rec.get("task_id", "") or "") != active_id:
            return

        active = self.gamble_queue.data.get("active") or {}
        user_key = str(active.get("user_key", "") or "")
        platform = str(active.get("platform", "") or "")
        reply_name = str(active.get("reply_name", "") or "User")
        cmd_name = str(active.get("command", "slots") or "slots")
        if cmd_name.lower() == "gamble":
            cmd_name = "slots"

        # Keep slots settings hot-reloadable.
        self._refresh_slots_cfg()
        cfg = self.slots_cfg
        if isinstance(active.get("slots_cfg"), dict):
            # If the queued task captured a config snapshot, prefer it.
            cfg = _normalize_slots_cfg(active.get("slots_cfg"))

        game = rec.get("game") or {}

        # Bet comes from the active task (source of truth), but allow worker override.
        bet = int(game.get("bet", active.get("bet", 0)) or 0)
        result_code = str(game.get("result_code", "") or "")

        symbols = _coerce_symbols(game.get("symbols") or game.get("result") or game.get("spin") or game.get("reels"))
        if not symbols:
            # Alternate worker formats
            maybe = [game.get("s1"), game.get("s2"), game.get("s3")]
            if any(x is not None for x in maybe):
                symbols = [str(x) for x in maybe if x is not None][:3]

        # Multiplier can be computed by router (config-based) or provided by worker.
        mult_raw = game.get("multiplier", game.get("mult", None))
        mult_from_game = None
        if mult_raw is not None:
            try:
                mult_from_game = int(mult_raw)
            except Exception:
                mult_from_game = None

        if mult_from_game is not None:
            mult = mult_from_game
            rule_name = str(game.get("rule_name", "") or game.get("rule", "") or result_code or "WIN")
            rc = result_code or "SLOTS_CUSTOM"
            syms = symbols or list(DEFAULT_RESULTCODE_SYMBOLS.get(result_code, ["?", "?", "?"]))
        else:
            mult, rule_name, rc, syms = eval_slots(symbols, result_code, cfg)

        # Gross payout: points returned (including the wager) for a win.
        payout_raw = game.get("payout", game.get("payout_points", game.get("win_points", None)))
        if payout_raw is None:
            payout = int(bet) * int(mult)
        else:
            try:
                payout = int(payout_raw)
            except Exception:
                payout = int(bet) * int(mult)
        if payout < 0:
            payout = 0

        pts_before = self.get_points(user_key)
        net = int(payout) - int(bet)
        pts_after = max(0, int(pts_before) + int(net))
        self.set_points(user_key, pts_after)

        sym_disp = " | ".join([str(x) for x in (syms or [])][:3])
        if not sym_disp:
            sym_disp = "? | ? | ?"

        if int(mult) > 0 and int(payout) > 0:
            # Example: bet=50, mult=3 => payout=150, net=+100
            result_line = f"🎰 Slots: [{sym_disp}] — WIN x{mult}! Won {payout} pts (net +{net} pts). Total: {pts_after} pts."
        else:
            result_line = f"🎰 Slots: [{sym_disp}] — You lose. Lost {bet} pts. Total: {pts_after} pts."

        # Receipt for the command resolution.
        result_line += f" Receipt: !{cmd_name} cost {bet} pts. New total: {pts_after} pts."
        self.emit_reply(platform, reply_name, result_line, bot_id="gamble")

        # Ledger/audit
        try:
            note = f"slots; rule={rule_name}; result_code={rc}; symbols={sym_disp}; bet={bet}; mult={mult}; payout={payout}; net={net}"
        except Exception:
            note = "slots"
        self.record_ledger(user_key, platform, cmd_name, "gamble", net, pts_before, pts_after, note=note)

        # forward overlay events
        for ev in (rec.get("overlay_events") or []):
            self.emit_overlay(
                overlay=str(ev.get("overlay", "casino") or "casino"),
                event=str(ev.get("event", "") or ""),
                payload=ev.get("payload") or {},
                event_id=f"evt_{active_id}",
            )

        blocking_ms = int(rec.get("blocking_ms", 0) or 0)
        self.gamble_queue.mark_done(blocking_ms=blocking_ms)


    # ---------- dispatch gamble FIFO ----------
    def maybe_dispatch_gamble(self) -> None:
        if "gamble" not in self.bots:
            return
        if not self.gamble_queue.can_dispatch(int(time.time())):
            return
        task = self.gamble_queue.pop_next_for_dispatch()
        if not task:
            return
        append_jsonl(self.bots["gamble"]["inbox"], task)

    # ---------- events + points ----------
    def award_active_points_tick(self) -> None:
        now = int(time.time())
        if now - self._last_active_award_ts < 5:
            return
        self._last_active_award_ts = now

        for user_key, rec in list(self.user_state.items()):
            if not isinstance(rec, dict):
                continue
            last_seen = int(rec.get("last_seen_ts", 0) or 0)
            if now - last_seen > self.active_window_seconds:
                continue
            last_award = int(rec.get("last_award_ts", now) or now)
            elapsed = now - last_award
            if elapsed < 60:
                continue
            minutes = elapsed // 60
            add = minutes * self.points_per_minute_active
            if add > 0:
                rec["points"] = max(0, int(rec.get("points", 0) or 0) + add)
                rec["last_award_ts"] = last_award + minutes * 60
                self._dirty_user_state = True

    def process_event(self, ev: Dict[str, Any]) -> None:
        platform = str(ev.get("platform", "unknown") or "unknown").lower()
        user_key = str(ev.get("user_key", "") or "")
        reply_name = str(ev.get("reply_name", "") or "User")
        user_tier = str(ev.get("tier", "EVERYONE") or "EVERYONE").upper()
        etype = str(ev.get("type", "chat") or "chat").lower()
        text = ev.get("text", "")

        # update last seen
        urec = self._get_user_rec(user_key)
        urec["last_seen_ts"] = int(time.time())
        self._dirty_user_state = True

        # earning
        if etype == "chat":
            if self.points_per_message:
                self.add_points(user_key, self.points_per_message)

            parsed = self.parse_command(text if isinstance(text, str) else "")
            if not parsed:
                return
            cmd, args = parsed

            # --- Dedup guard ---
            # Exact key includes event ts (best case). Loose key ignores ts and
            # only blocks for a very short window.
            try:
                nowf = time.time()
                ev_ts = int(ev.get("ts", 0) or 0)
                base = f"{platform}|{user_key}|{reply_name}|{cmd}|{args}"
                k_exact = f"{base}|{ev_ts}"
                k_loose = base

                # purge old
                if self._recent_cmd_exact:
                    cut = nowf - self._recent_cmd_window_exact_sec
                    for k, t0 in list(self._recent_cmd_exact.items()):
                        if t0 < cut:
                            self._recent_cmd_exact.pop(k, None)
                if self._recent_cmd_loose:
                    cut = nowf - self._recent_cmd_window_loose_sec
                    for k, t0 in list(self._recent_cmd_loose.items()):
                        if t0 < cut:
                            self._recent_cmd_loose.pop(k, None)

                if k_exact in self._recent_cmd_exact:
                    return
                if k_loose in self._recent_cmd_loose:
                    return

                self._recent_cmd_exact[k_exact] = nowf
                self._recent_cmd_loose[k_loose] = nowf
            except Exception:
                pass

            # manager command
            if cmd in self.manager_commands:
                self.handle_manager_command(self.manager_commands[cmd], platform, reply_name, user_key, user_tier)
                return

            # bot command
            if cmd in self.commands:
                cdef = self.commands[cmd]
                self.handle_bot_command(cdef, platform, reply_name, user_key, user_tier, args)
                return

        elif etype == "like":
            if self.points_per_like:
                self.add_points(user_key, self.points_per_like)
        elif etype == "share":
            if self.points_per_share:
                self.add_points(user_key, self.points_per_share)

    # ---------- polling ----------
    def poll_events(self) -> None:
        off = int(self.offsets.get("events_in_offset_bytes", 0) or 0)
        recs, off2 = read_new_jsonl(self.events_in, off)
        if off2 != off:
            self.offsets["events_in_offset_bytes"] = off2
            self._dirty_offsets = True
        for r in recs:
            if isinstance(r, dict):
                self.process_event(r)

    def poll_bot_outboxes(self) -> None:
        bot_offsets = self.offsets.get("bot_offsets") or {}
        for bot_id, paths in self.bots.items():
            bo = bot_offsets.get(bot_id) or {"outbox_offset_bytes": 0, "ack_offset_bytes": 0}

            out_off = int(bo.get("outbox_offset_bytes", 0) or 0)
            out_recs, out_off2 = read_new_jsonl(paths["outbox"], out_off)
            if out_off2 != out_off:
                bo["outbox_offset_bytes"] = out_off2
                bot_offsets[bot_id] = bo
                self.offsets["bot_offsets"] = bot_offsets
                self._dirty_offsets = True

            for rec in out_recs:
                if isinstance(rec, dict):
                    self.handle_worker_reply(bot_id, rec)

            ack_off = int(bo.get("ack_offset_bytes", 0) or 0)
            _acks, ack_off2 = read_new_jsonl(paths["ack"], ack_off)
            if ack_off2 != ack_off:
                bo["ack_offset_bytes"] = ack_off2
                bot_offsets[bot_id] = bo
                self.offsets["bot_offsets"] = bot_offsets
                self._dirty_offsets = True

    def _mirror_user_state(self) -> None:
        if not getattr(self, 'user_state_mirror_path', None):
            return
        try:
            atomic_write_json(self.user_state_mirror_path, self.user_state)
        except Exception:
            self.log.exception('Failed to mirror user_state')

    def flush(self) -> None:
        if self._dirty_user_state:
            atomic_write_json(self.user_state_path, self.user_state)
            self._mirror_user_state()
            self._dirty_user_state = False
        if self._dirty_inflight:
            atomic_write_json(self.inflight_path, self.inflight)
            self._dirty_inflight = False
        if self._dirty_offsets:
            atomic_write_json(self.offsets_path, self.offsets)
            self._dirty_offsets = False

    def run(self) -> None:
        self.log.info("Started")
        self.log.info("events_in=%s", str(self.events_in))
        self.log.info("replies_out=%s", str(self.replies_out))
        self.log.info("overlay_out=%s", str(self.overlay_out))
        self.log.info("bots=%s", list(self.bots.keys()))

        while True:
            try:
                self.award_active_points_tick()
                self.poll_events()
                self.poll_bot_outboxes()
                self.maybe_dispatch_gamble()
                self.flush()
                time.sleep(max(0.05, self.poll_ms / 1000.0))
            except Exception as e:
                self.log.exception("Loop error")
                time.sleep(0.5)


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    RouterBank(base_dir).run()


if __name__ == "__main__":
    main()
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
