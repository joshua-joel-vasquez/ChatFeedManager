from typing import Dict, List
from .roles import has_access, TIER_ORDER


def build_help_lines(
    manager_cmds: List[dict],
    bot_cmds: List[dict],
    u_tier_val: int,
    points_balance: int,
) -> List[str]:
    """
    Shows only commands available now:
      - tier access
      - points affordability
    Output intentionally has NO leading "!" to avoid accidental triggering.
    """
    lines: List[str] = []
    lines.append("SpotifyBot Commands")
    lines.append('Every command starts with "!".')
    lines.append("")

    def can_use(c: dict) -> bool:
        if not has_access(u_tier_val, (c.get("min_tier") or "EVERYONE").upper()):
            return False
        cost = int(c.get("cost_points") or 0)
        if cost > 0 and points_balance < cost:
            return False
        return True

    # Manager section
    mgr_visible = [c for c in manager_cmds if c.get("show_in_help", True) and can_use(c)]
    if mgr_visible:
        lines.append("[manager]")
        for c in mgr_visible:
            for hl in (c.get("help_lines") or []):
                lines.append(str(hl))
        lines.append("")

    # Bots grouped
    grouped: Dict[str, List[dict]] = {}
    for c in bot_cmds:
        bot = (c.get("bot") or "").strip().lower()
        grouped.setdefault(bot, []).append(c)

    for bot in sorted(grouped.keys()):
        visible = [c for c in grouped[bot] if c.get("show_in_help", True) and can_use(c)]
        if not visible:
            continue
        lines.append(f"[{bot}]")
        for c in sorted(visible, key=lambda x: str(x.get("command") or "")):
            for hl in (c.get("help_lines") or []):
                lines.append(str(hl))
        lines.append("")

    while lines and lines[-1].strip() == "":
        lines.pop()

    if len(lines) <= 3:
        return ["No commands available for your role/points right now."]

    return lines
