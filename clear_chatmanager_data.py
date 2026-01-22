import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


def ts_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def backup_file(src: Path, backup_root: Path, bot_root: Path) -> None:
    if not src.exists():
        return
    rel = src.resolve().relative_to(bot_root.resolve())
    dst = backup_root / rel
    safe_mkdir(dst.parent)
    shutil.copy2(src, dst)


def truncate_file(p: Path) -> None:
    safe_mkdir(p.parent)
    p.write_text("", encoding="utf-8")


def delete_file(p: Path) -> None:
    if p.exists():
        p.unlink()


def glob_files(base: Path, pattern: str) -> list[Path]:
    if not base.exists():
        return []
    return [x for x in base.rglob(pattern) if x.is_file()]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Clear ChatManager pipeline/state/logs (with optional backups). "
                    "Run this with services stopped."
    )

    ap.add_argument("--pipeline", action="store_true", help="Clear ChatManager bus pipeline files (*.jsonl).")
    ap.add_argument("--state", action="store_true", help="Clear ChatManager state (offsets/inflight/gamble queue).")
    ap.add_argument("--reset-points", action="store_true", help="Also wipe user_state.json (points/ranks).")
    ap.add_argument("--overlay", action="store_true", help="Clear overlay extras (overlay_additions/events + mirrored user_state).")
    ap.add_argument("--logs", action="store_true", help="Clear bot/logs/*.log (including latest.log).")
    ap.add_argument("--all", action="store_true", help="Do everything (pipeline + state + overlay + logs).")

    ap.add_argument("--no-backup", action="store_true", help="Do not back up files before clearing.")
    ap.add_argument("--yes", action="store_true", help="Do not prompt for confirmation.")
    args = ap.parse_args()

    bot_root = Path(__file__).resolve().parent
    chatmanager = bot_root / "ChatManager"
    cm_bus = chatmanager / "bus"
    cm_state = chatmanager / "state"
    overlay_dir = bot_root / "Overlays" / "UnifiedChat"
    logs_dir = bot_root / "logs"

    if not chatmanager.exists():
        print(f"[clear] ERROR: ChatManager folder not found at: {chatmanager}")
        return 2

    if args.all:
        args.pipeline = True
        args.state = True
        args.overlay = True
        args.logs = True

    if not (args.pipeline or args.state or args.overlay or args.logs or args.reset_points):
        print("[clear] Nothing selected. Use --pipeline/--state/--overlay/--logs or --all.")
        return 2

    # Build target list
    to_truncate: list[Path] = []
    to_delete: list[Path] = []

    # PIPELINE: truncate all jsonl in ChatManager/bus
    if args.pipeline:
        to_truncate += glob_files(cm_bus, "*.jsonl")

    # STATE: delete offsets/inflight/gamble queue; optionally wipe user_state.json
    if args.state:
        # offsets + inflight + gamble queue (delete so services re-init cleanly)
        for name in [
            "offsets.ingestor.json",
            "offsets.router.json",
            "offsets.emitter.json",
            "inflight.json",
            "gamble_queue.json",
        ]:
            to_delete.append(cm_state / name)

        # some projects also keep other offsets; delete any offsets.*.json
        to_delete += glob_files(cm_state, "offsets.*.json")

        # keep user_state unless reset requested
        if args.reset_points:
            to_delete.append(cm_state / "user_state.json")

    # OVERLAY: truncate overlay extras + (optional) mirror user_state
    if args.overlay:
        # common overlay extras created by emitter
        to_truncate.append(overlay_dir / "overlay_additions.jsonl")
        to_truncate.append(overlay_dir / "overlay_events.jsonl")

        # mirrored points state for overlay
        to_delete.append(overlay_dir / "user_state.json")

    # LOGS: truncate all *.log in bot/logs
    if args.logs:
        to_truncate += glob_files(logs_dir, "*.log")

    # Remove duplicates and enforce safety (stay under bot root)
    seen = set()
    trunc_unique: list[Path] = []
    for p in to_truncate:
        p = p.resolve() if p.exists() else p
        if str(p) in seen:
            continue
        if not is_under(p, bot_root):
            continue
        seen.add(str(p))
        trunc_unique.append(p)

    del_unique: list[Path] = []
    for p in to_delete:
        p = p.resolve() if p.exists() else p
        if str(p) in seen:
            continue
        if not is_under(p, bot_root):
            continue
        seen.add(str(p))
        del_unique.append(p)

    print("\n[clear] Target bot root:", bot_root)
    print("[clear] Will TRUNCATE (empty) these files:")
    for p in trunc_unique:
        print("   -", p.relative_to(bot_root) if is_under(p, bot_root) else p)

    print("\n[clear] Will DELETE these files:")
    for p in del_unique:
        rel = p.relative_to(bot_root) if is_under(p, bot_root) else p
        print("   -", rel)

    if not args.yes:
        resp = input("\nType YES to proceed: ").strip()
        if resp != "YES":
            print("[clear] Cancelled.")
            return 1

    # Backup
    backup_root = bot_root / ".backup_clear" / ts_stamp()
    if not args.no_backup:
        safe_mkdir(backup_root)
        for p in trunc_unique + del_unique:
            try:
                if p.exists():
                    backup_file(p, backup_root, bot_root)
            except Exception as e:
                print(f"[clear] WARN: backup failed for {p}: {e}")

        print(f"\n[clear] Backup saved to: {backup_root}")

    # Apply truncations
    for p in trunc_unique:
        try:
            truncate_file(p)
        except Exception as e:
            print(f"[clear] ERROR: could not truncate {p}: {e}")

    # Apply deletions
    for p in del_unique:
        try:
            delete_file(p)
        except Exception as e:
            print(f"[clear] ERROR: could not delete {p}: {e}")

    print("\n[clear] Done.")
    print("[clear] Tip: start with a clean run using: py run_all.py --same-console")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
