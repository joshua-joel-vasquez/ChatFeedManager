from __future__ import annotations
from typing import Any, Dict
import random

from games.slots import play_slots


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def run_game_task(task: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """
    Returns payload merged into outbox reply record:
      {
        "game": {...},
        "messages": [...],
        "overlay_events": [...],
        "blocking_ms": ms
      }
    """
    action = str(task.get("action", "")).strip().lower()
    bet = _safe_int(task.get("bet", 0), 0)
    available = _safe_int(task.get("available_points", 0), 0)

    # Worker-side guardrails (ChatManager is still the bank)
    if bet <= 0:
        return {
            "game": {"name": action or "unknown", "bet": bet, "result_code": "INVALID_BET", "payout": 0},
            "messages": ["🎰 Invalid bet. Use `!slots <amount>` or `!slot <amount>` (or `max`)."],
            "overlay_events": [],
            "blocking_ms": 0,
        }

    # Optional clamp if manager passes available_points
    if available > 0 and bet > available:
        bet = available

    reply_name = str(task.get("reply_name", "Player"))

    # Host multiple games under one worker
    if action == "slots":
        result = play_slots(bet=bet, player_name=reply_name, rng=rng)

        overlay_event = {
            "overlay": "casino",
            "event": "slots_spin",
            "payload": {
                "player_name": reply_name,
                "bet": bet,
                "reels": result["reels"],
                "tier": result["tier"],
                "payout": result["payout"],
                "animation": result["animation"],
                "spin_ms": result["spin_ms"],
            }
        }

        msg = result["message"].format(player=reply_name, bet=bet, payout=result["payout"])

        return {
            "game": {
                "name": "slots",
                "bet": bet,
                "result_code": result["result_code"],
                "payout": result["payout"],  # manager will validate
                "symbols": result["reels"],
                "reels": result["reels"],
            },
            "messages": [msg],
            "overlay_events": [overlay_event],
            "blocking_ms": int(result["spin_ms"]),
        }

    return {
        "game": {"name": action or "unknown", "bet": bet, "result_code": "UNKNOWN_GAME", "payout": 0},
        "messages": [f"🎰 Unknown game action: {action}"],
        "overlay_events": [],
        "blocking_ms": 0,
    }
