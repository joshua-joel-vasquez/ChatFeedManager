from typing import Any, Dict

TIER_ORDER = {"EVERYONE": 0, "SUB": 1, "VIP": 2, "MOD": 3, "BROADCASTER": 4}


def user_tier(user: Dict[str, Any]) -> int:
    if user.get("isBroadcaster"):
        return TIER_ORDER["BROADCASTER"]
    if user.get("isMod"):
        return TIER_ORDER["MOD"]
    if user.get("isVip"):
        return TIER_ORDER["VIP"]
    if user.get("isSubscriber"):
        return TIER_ORDER["SUB"]
    return TIER_ORDER["EVERYONE"]


def has_access(u_tier_val: int, min_tier: str) -> bool:
    return u_tier_val >= TIER_ORDER.get((min_tier or "EVERYONE").upper(), 0)


def tier_name(u_tier_val: int) -> str:
    inv = {v: k for k, v in TIER_ORDER.items()}
    return inv.get(u_tier_val, "EVERYONE")
