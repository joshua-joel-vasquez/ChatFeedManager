import argparse
import os
import platform
import signal
import subprocess
import sys
import time
from typing import List, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))

PATH_SUPERVISOR = os.path.join(ROOT, "ChatSupervisor", "supervisor_inspector.py")

HOST_IS_WINDOWS = (platform.system().lower() == "windows")

# How we treat process spawning / shutdown. This is a *runtime* choice so you can
# run the same repo on different machines with predictable behavior.
#
# - windows: uses taskkill /T to reliably kill the whole supervisor tree
# - mac:     uses POSIX process groups (start_new_session + killpg)
# - auto:    choose based on the current host OS
OS_CHOICES = ("auto", "windows", "mac")

# Resolved at runtime (in main) from --os.
OS_MODE = "windows" if HOST_IS_WINDOWS else "mac"


def resolve_os_mode(requested: str) -> str:
    r = (requested or "auto").strip().lower()
    if r not in OS_CHOICES:
        return "auto"
    if r == "auto":
        return "windows" if HOST_IS_WINDOWS else "mac"
    # Best-effort: if user forces a mode that doesn't match the host, fall back.
    if r == "windows" and not HOST_IS_WINDOWS:
        print("[run_all] NOTE: --os windows requested, but host is not Windows. Falling back to mac mode.")
        return "mac"
    if r == "mac" and HOST_IS_WINDOWS:
        print("[run_all] NOTE: --os mac requested, but host is Windows. Falling back to windows mode.")
        return "windows"
    return r

PROCS: List[subprocess.Popen] = []


def ensure_file(path: str, name: str) -> None:
    if not os.path.isfile(path):
        raise RuntimeError(f"Missing file for {name}: {path}")


def _taskkill_tree(pid: int) -> None:
    # Windows-only: force kill process tree
    if OS_MODE != "windows":
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass


def _terminate_then_kill(p: subprocess.Popen) -> None:
    """Terminate the supervisor + everything it started.

    Why this exists:
      - On Windows, taskkill /T is the most reliable way to kill a whole tree.
      - On mac/Linux, we start the supervisor in its own process group
        (start_new_session=True) and then signal the whole group.
    """

    if p.poll() is not None:
        return

    # Windows: kill the full tree immediately.
    if OS_MODE == "windows":
        _taskkill_tree(p.pid)
        return

    # mac/Linux: try process-group termination first.
    used_pgroup = False
    if hasattr(os, "killpg"):
        try:
            os.killpg(p.pid, signal.SIGTERM)
            used_pgroup = True
        except Exception:
            used_pgroup = False

    if not used_pgroup:
        # Fallback: just terminate the parent.
        try:
            p.terminate()
        except Exception:
            pass

    # Give it a moment to exit gracefully
    for _ in range(20):
        if p.poll() is not None:
            return
        time.sleep(0.1)

    # Force kill
    if hasattr(os, "killpg") and used_pgroup:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass
    try:
        if p.poll() is None:
            p.kill()
    except Exception:
        pass


def start_supervisor(args) -> subprocess.Popen:
    ensure_file(PATH_SUPERVISOR, "ChatSupervisor/supervisor_inspector.py")

    py = sys.executable
    sup_cmd = [py, PATH_SUPERVISOR]

    if args.same_console:
        sup_cmd.append("--same-console")
    if args.no_servers:
        sup_cmd.append("--no-servers")
    if args.skip_writer:
        sup_cmd.append("--skip-writer")
    if args.no_workers:
        sup_cmd.append("--no-workers")

    sup_cmd += ["--overlay-port", str(args.overlay_port)]
    sup_cmd += ["--manager-port", str(args.manager_port)]

    if args.restart_stale:
        sup_cmd.append("--restart-stale")
        sup_cmd += ["--stale-services", str(args.stale_services)]
        sup_cmd += ["--stale-workers", str(args.stale_workers)]
        sup_cmd += ["--check-every", str(args.check_every)]
        sup_cmd += ["--status-every", str(args.status_every)]

    if args.allow_duplicate_inbox:
        sup_cmd.append("--allow-duplicate-inbox")

    # Ensure the supervisor uses the same process-management mode as this launcher.
    # Pass the resolved OS_MODE (not the raw --os arg) to avoid mismatches.
    sup_cmd += ["--os", OS_MODE]

    print("[run_all] Starting ChatSupervisor (recommended).")
    print(f"[run_all]   cmd: {' '.join(sup_cmd)}")

    creationflags = 0
    start_new_session = False

    # Windows: optional process group (taskkill /T is the reliable stop anyway)
    if OS_MODE == "windows" and HOST_IS_WINDOWS:
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP

    # mac/Linux: isolate the supervisor into its own process group so Ctrl+C kills EVERYTHING.
    if OS_MODE == "mac" and not HOST_IS_WINDOWS:
        start_new_session = True

    p = subprocess.Popen(
        sup_cmd,
        cwd=ROOT,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    PROCS.append(p)
    return p


def kill_all() -> None:
    for p in list(PROCS):
        _terminate_then_kill(p)
    PROCS.clear()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the app stack. Default runs ChatSupervisor (starts + monitors everything)."
    )

    ap.add_argument("--no-servers", action="store_true", help="Do not start http.server processes (OBS overlays).")
    ap.add_argument("--same-console", action="store_true", help="Run everything in the same console.")
    ap.add_argument("--skip-writer", action="store_true", help="Skip SSNChatWriter.")
    ap.add_argument("--no-workers", action="store_true", help="Do not start worker bots.")

    ap.add_argument("--overlay-port", type=int, default=8080)
    ap.add_argument("--manager-port", type=int, default=8788)

    ap.add_argument("--restart-stale", action="store_true", help="Restart services/workers when stuck.")
    ap.add_argument("--stale-services", type=float, default=45.0)
    ap.add_argument("--stale-workers", type=float, default=60.0)
    ap.add_argument("--check-every", type=float, default=0.5)
    ap.add_argument("--status-every", type=float, default=2.0)

    ap.add_argument("--allow-duplicate-inbox", action="store_true",
                    help="Allow multiple worker instances on same inbox (CAN duplicate processing).")

    ap.add_argument(
        "--os",
        choices=OS_CHOICES,
        default="auto",
        help="Force process management mode (auto/windows/mac). Usually leave as auto.",
    )

    args = ap.parse_args()

    global OS_MODE
    OS_MODE = resolve_os_mode(getattr(args, "os", "auto"))
    print(f"[run_all] OS mode: {OS_MODE} (host: {platform.system()})")

    # Ctrl+C handler
    def _handle(sig, frame):
        print("\n[run_all] stopping everything...")
        kill_all()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handle)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle)

    sup = start_supervisor(args)

    print("\n[run_all] Running. Press Ctrl+C to stop EVERYTHING.\n")

    try:
        while True:
            rc = sup.poll()
            if rc is not None:
                print(f"[run_all] Supervisor exited code={rc}. Shutting down any remaining processes...")
                kill_all()
                return rc
            time.sleep(0.5)
    except KeyboardInterrupt:
        _handle(None, None)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
