from __future__ import annotations
from typing import Any, Dict, List
import random

# Weighted symbols -> emojis
SYMBOLS = [
    ("CHERRY", "🍒", 22),
    ("LEMON",  "🍋", 18),
    ("GRAPE",  "🍇", 16),
    ("DIAMOND","💎", 8),
    ("BAR",    "🟥", 5),
    ("SEVEN",  "7️⃣", 3),
]

# Deterministic result codes so ChatManager can validate payouts
RULES = {
    "SLOTS_777":           {"mult": 25, "tier": "jackpot",   "animation": "slots_jackpot_v1", "spin_ms": 3200},
    "SLOTS_TRIPLE_BAR":    {"mult": 15, "tier": "big_win",   "animation": "slots_bigwin_v1",  "spin_ms": 2600},
    "SLOTS_TRIPLE_CHERRY": {"mult": 8,  "tier": "big_win",   "animation": "slots_bigwin_v1",  "spin_ms": 2400},
    "SLOTS_DOUBLE_7":      {"mult": 3,  "tier": "win",       "animation": "slots_win_v1",     "spin_ms": 2200},
    "SLOTS_DOUBLE_CHERRY": {"mult": 2,  "tier": "win",       "animation": "slots_win_v1",     "spin_ms": 2100},
    "SLOTS_SINGLE_CHERRY": {"mult": 1,  "tier": "small_win", "animation": "slots_small_v1",   "spin_ms": 1900},
    "SLOTS_LOSS":          {"mult": 0,  "tier": "loss",      "animation": "slots_loss_v1",    "spin_ms": 1700},
}


def _weighted_choice(rng: random.Random) -> str:
    total = sum(w for _, _, w in SYMBOLS)
    r = rng.randrange(total)
    upto = 0
    for _token, emoji, w in SYMBOLS:
        upto += w
        if r < upto:
            return emoji
    return SYMBOLS[0][1]


def _classify(reels: List[str]) -> str:
    seven = "7️⃣"
    bar = "🟥"
    cherry = "🍒"

    if reels == [seven, seven, seven]:
        return "SLOTS_777"
    if reels == [bar, bar, bar]:
        return "SLOTS_TRIPLE_BAR"
    if reels == [cherry, cherry, cherry]:
        return "SLOTS_TRIPLE_CHERRY"
    if sum(1 for x in reels if x == seven) == 2:
        return "SLOTS_DOUBLE_7"
    if sum(1 for x in reels if x == cherry) == 2:
        return "SLOTS_DOUBLE_CHERRY"
    if sum(1 for x in reels if x == cherry) == 1:
        return "SLOTS_SINGLE_CHERRY"
    return "SLOTS_LOSS"


def play_slots(bet: int, player_name: str, rng: random.Random) -> Dict[str, Any]:
    reels = [_weighted_choice(rng), _weighted_choice(rng), _weighted_choice(rng)]
    code = _classify(reels)
    rule = RULES[code]
    payout = int(bet * rule["mult"])

    if payout <= 0:
        msg = "🎰 {player} spun " + " ".join(reels) + " — no win. (-{bet})"
    else:
        msg = "🎰 {player} spun " + " ".join(reels) + f" — {rule['tier'].upper()}! (+{{payout}} | bet {{bet}})"

    return {
        "result_code": code,
        "payout": payout,
        "reels": reels,
        "tier": rule["tier"],
        "animation": rule["animation"],
        "spin_ms": rule["spin_ms"],
        "message": msg,
    }
