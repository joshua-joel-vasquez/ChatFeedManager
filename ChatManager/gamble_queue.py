from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
from pathlib import Path
import json
import time


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
    command: str = "slots"


class GambleQueue:
    """
    Authoritative FIFO inside ChatManager:
      - queue tasks
      - only one active at a time
      - busy_until_ts to avoid overlay animation overlap
    """
    def __init__(self, path: Path):
        self.path = path
        self.data = _load_json(self.path, {"queue": [], "active": None, "busy_until_ts": 0})

    def save(self) -> None:
        _atomic_write_json(self.path, self.data)

    def reserved_points_for_user(self, user_key: str) -> int:
        total = 0
        for t in self.data.get("queue", []):
            if t.get("user_key") == user_key:
                total += int(t.get("bet", 0))
        a = self.data.get("active")
        if a and a.get("user_key") == user_key:
            total += int(a.get("bet", 0))
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
        if now_ts < int(self.data.get("busy_until_ts", 0)):
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
        # Convert ms to seconds for busy window
        if blocking_ms and blocking_ms > 0:
            self.data["busy_until_ts"] = int(time.time()) + max(0, int(blocking_ms)) // 1000
        else:
            self.data["busy_until_ts"] = int(time.time())
        self.save()
