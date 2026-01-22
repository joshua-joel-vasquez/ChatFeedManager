import argparse
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

IS_WINDOWS = (platform.system().lower() == "windows")


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.25)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        try:
            s.close()
        except Exception:
            pass


def load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def safe_touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ProcSpec:
    name: str
    cmd: List[str]
    cwd: Path
    env: Optional[Dict[str, str]] = None
    new_console: bool = False
    restart: bool = True
    max_restarts: int = 30
    restart_window_sec: int = 300
    backoff_sec: float = 1.0


@dataclass
class ProcState:
    spec: ProcSpec
    popen: Optional[subprocess.Popen] = None
    start_ts: float = 0.0
    restarts: Optional[List[float]] = None
    last_restart_reason: str = ""
    _printed_standby: bool = False

    def __post_init__(self):
        if self.restarts is None:
            self.restarts = []


class ChatSupervisor:
    """
    Starts + monitors:
      - optional overlay http.server(s)
      - SSNChatWriter
      - ChatManager microservices (ingestor/router/emitter)
      - worker bots (Spotify/Gamble/etc) as defined in ChatManager/commands.txt

    Supports HA active/standby workers via:
      bots[].ha = "active_standby"
      bots[].instances = 2 (or more)
    Supervisor sets:
      WORKER_ROLE=primary/secondary and CHAT_SUPERVISOR_INSTANCE=0/1/...
    """

    def __init__(self, bot_root: Path, args: argparse.Namespace):
        self.bot_root = bot_root
        self.args = args
        self.py = sys.executable

        self.chatmanager = bot_root / "ChatManager"
        self.overlay_dir = bot_root / "Overlays" / "UnifiedChat"
        self.ssn_dir = bot_root / "SSNChatWriter"
        self.logs_dir = bot_root / "logs"

        self.cfg = load_json(self.chatmanager / "commands.txt", {})
        self.procs: Dict[str, ProcState] = {}

        # Key bus/state files for health checks / visibility
        self.bus_dir = self.chatmanager / "bus"
        self.state_dir = self.chatmanager / "state"
        self.bus_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.events_in = self.bus_dir / "events.inbox.jsonl"
        self.replies_out = self.bus_dir / "replies.outbox.jsonl"
        safe_touch(self.events_in)
        safe_touch(self.replies_out)

        self.overlay_additions = self.overlay_dir / "overlay_additions.jsonl"
        self.overlay_events = self.overlay_dir / "overlay_events.jsonl"

        self.status_path = self.state_dir / "supervisor_status.json"

        # Worker bus meta (populated in build)
        self.worker_meta: Dict[str, Dict[str, Path]] = {}

        # activity tracking
        self.last_seen_activity: Dict[str, float] = {}

        self._running = True

    # ---------- process control ----------
    def _start(self, ps: ProcState) -> None:
        spec = ps.spec
        spec.cwd.mkdir(parents=True, exist_ok=True)

        creationflags = 0
        if IS_WINDOWS and spec.new_console:
            creationflags |= subprocess.CREATE_NEW_CONSOLE

        env = os.environ.copy()
        if spec.env:
            env.update({k: str(v) for k, v in spec.env.items()})

        print(f"[ChatSupervisor] START {spec.name}")
        print(f"[ChatSupervisor]   cwd: {spec.cwd}")
        print(f"[ChatSupervisor]   cmd: {' '.join(spec.cmd)}")

        ps.popen = subprocess.Popen(spec.cmd, cwd=str(spec.cwd), env=env, creationflags=creationflags)
        ps.start_ts = time.time()

    def _terminate(self, ps: ProcState) -> None:
        p = ps.popen
        if not p:
            return
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass

    def _kill(self, ps: ProcState) -> None:
        p = ps.popen
        if not p:
            return
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass

    def _restart(self, ps: ProcState, reason: str) -> None:
        spec = ps.spec
        if not spec.restart:
            return

        now = time.time()
        ps.restarts = [t for t in ps.restarts if (now - t) <= spec.restart_window_sec]
        if len(ps.restarts) >= spec.max_restarts:
            print(f"[ChatSupervisor] RESTART-LIMIT {spec.name} (not restarting). reason={reason}")
            return

        ps.restarts.append(now)
        ps.last_restart_reason = reason
        ps._printed_standby = False

        print(f"[ChatSupervisor] RESTART {spec.name} reason={reason}")
        self._terminate(ps)
        time.sleep(0.8)
        self._kill(ps)
        time.sleep(spec.backoff_sec)
        self._start(ps)

    def _add_proc(self, spec: ProcSpec) -> None:
        self.procs[spec.name] = ProcState(spec=spec)

    # ---------- worker discovery ----------
    def _find_worker_script(self, bot_id: str) -> Optional[Path]:
        """
        Looks for:
          bot/Bots/<bot_id>/worker.py  (case-insensitive folder)
          bot/<bot_id>/worker.py
        """
        bot_id_l = bot_id.lower()

        roots = []
        if (self.bot_root / "Bots").exists():
            roots.append(self.bot_root / "Bots")
        roots.append(self.bot_root)

        for root in roots:
            if not root.exists():
                continue
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                if d.name.lower() == bot_id_l:
                    w = d / "worker.py"
                    if w.exists():
                        return w
        return None

    def _instances_from_cfg(self, bot_id: str) -> int:
        bot_id_l = bot_id.lower().strip()
        for b in (self.cfg.get("bots") or []):
            if str(b.get("id", "")).strip().lower() == bot_id_l and b.get("enabled", True) is not False:
                try:
                    n = int(b.get("instances", 1) or 1)
                    return max(1, min(16, n))
                except Exception:
                    return 1
        return 1

    # ---------- build process list ----------
    def build(self) -> None:
        new_console = (not self.args.same_console)

        # Optional overlay servers
        if not self.args.no_servers:
            if not is_port_in_use(self.args.overlay_port):
                self._add_proc(ProcSpec(
                    name=f"http.overlay:{self.args.overlay_port}",
                    cmd=[self.py, "-m", "http.server", str(self.args.overlay_port)],
                    cwd=self.overlay_dir,
                    new_console=new_console,
                ))
            else:
                print(f"[ChatSupervisor] overlay port {self.args.overlay_port} already in use; not starting overlay http.server")

            if not is_port_in_use(self.args.manager_port):
                self._add_proc(ProcSpec(
                    name=f"http.chatmanager:{self.args.manager_port}",
                    cmd=[self.py, "-m", "http.server", str(self.args.manager_port)],
                    cwd=self.chatmanager,
                    new_console=new_console,
                ))
            else:
                print(f"[ChatSupervisor] manager port {self.args.manager_port} already in use; not starting ChatManager http.server")

        # SSN writer
        if not self.args.skip_writer:
            self._add_proc(ProcSpec(
                name="SSNChatWriter",
                cmd=[self.py, "ssn_chat_feed_writer.py"],
                cwd=self.ssn_dir,
                new_console=new_console,
            ))

        # ChatManager microservices
        self._add_proc(ProcSpec(
            name="CM.ingestor",
            cmd=[self.py, "-m", "services.ingestor"],
            cwd=self.chatmanager,
            new_console=new_console,
        ))
        self._add_proc(ProcSpec(
            name="CM.router_bank",
            cmd=[self.py, "-m", "services.router_bank"],
            cwd=self.chatmanager,
            new_console=new_console,
        ))
        self._add_proc(ProcSpec(
            name="CM.emitter",
            cmd=[self.py, "-m", "services.emitter"],
            cwd=self.chatmanager,
            new_console=new_console,
        ))

        if self.args.no_workers:
            return

        # Workers from commands.txt "bots"
        for b in (self.cfg.get("bots") or []):
            if not isinstance(b, dict):
                continue
            if b.get("enabled", True) is False:
                continue

            bot_id = str(b.get("id", "") or "").strip().lower()
            if not bot_id:
                continue

            worker = self._find_worker_script(bot_id)
            if not worker:
                print(f"[ChatSupervisor] WARNING: worker.py not found for bot id '{bot_id}'")
                continue

            ha = str(b.get("ha", "") or "").strip().lower()
            cfg_instances = self._instances_from_cfg(bot_id)

            # Multi-instance rules:
            # - active_standby => allow instances (primary/secondary), because only leader should “touch outside”
            # - otherwise => default to 1 unless --allow-duplicate-inbox (duplicates are possible)
            if cfg_instances > 1:
                if ha == "active_standby":
                    instances = cfg_instances
                elif self.args.allow_duplicate_inbox:
                    instances = cfg_instances
                else:
                    print(
                        f"[ChatSupervisor] NOTE: '{bot_id}' has instances={cfg_instances} but "
                        f"no HA mode; starting 1 instance only to prevent duplicates."
                    )
                    instances = 1
            else:
                instances = 1

            # Ensure bus files exist for monitoring
            inbox = self.bus_dir / f"{bot_id}.inbox.jsonl"
            outbox = self.bus_dir / f"{bot_id}.outbox.jsonl"
            ack = self.bus_dir / f"{bot_id}.ack.jsonl"
            safe_touch(inbox)
            safe_touch(outbox)
            safe_touch(ack)
            self.worker_meta[bot_id] = {"inbox": inbox, "outbox": outbox, "ack": ack}

            for i in range(instances):
                env = {
                    "CHAT_SUPERVISOR_BOT_ID": bot_id,
                    "CHAT_SUPERVISOR_INSTANCE": str(i),
                }
                if ha == "active_standby":
                    env["WORKER_ROLE"] = "primary" if i == 0 else "secondary"

                # Run worker.py from its folder
                self._add_proc(ProcSpec(
                    name=f"W.{bot_id}#{i}",
                    cmd=[self.py, "worker.py"],
                    cwd=worker.parent,
                    env=env,
                    new_console=new_console,
                ))

    # ---------- health checks ----------
    def _health_activity_sources(self) -> Dict[str, List[Path]]:
        sources: Dict[str, List[Path]] = {
            "CM.ingestor": [self.events_in],
            "CM.router_bank": [self.replies_out] + [v["inbox"] for v in self.worker_meta.values()],
            "CM.emitter": [self.overlay_additions, self.overlay_events],
        }
        for bot_id, meta in self.worker_meta.items():
            sources[f"W.{bot_id}"] = [meta["ack"], meta["outbox"]]
        return sources

    def _is_stale(self, key: str, paths: List[Path], stale_sec: float) -> bool:
        newest = 0.0
        for p in paths:
            newest = max(newest, mtime(p))
        if newest <= 0:
            return False
        self.last_seen_activity[key] = newest
        age = time.time() - newest
        return age > stale_sec

    def _worker_backlog_stale(self, bot_id: str, stale_sec: float) -> bool:
        meta = self.worker_meta.get(bot_id)
        if not meta:
            return False
        inbox_m = mtime(meta["inbox"])
        ack_m = mtime(meta["ack"])
        if inbox_m <= 0:
            return False
        # If inbox is newer than ack for too long => worker stuck
        if inbox_m > ack_m:
            return (time.time() - inbox_m) > stale_sec
        return False

    def _write_status(self) -> None:
        status = {
            "ts": now_ms(),
            "procs": {},
            "activity": {k: v for k, v in self.last_seen_activity.items()},
        }

        for name, ps in self.procs.items():
            p = ps.popen
            alive = (p is not None and p.poll() is None)
            status["procs"][name] = {
                "alive": alive,
                "pid": (p.pid if p else None),
                "start_ts": ps.start_ts,
                "restarts_in_window": len(ps.restarts or []),
                "last_restart_reason": ps.last_restart_reason,
                "cmd": ps.spec.cmd,
                "cwd": str(ps.spec.cwd),
            }

        atomic_write_json(self.status_path, status)

    # ---------- lifecycle ----------
    def stop_all(self) -> None:
        print("\n[ChatSupervisor] stopping all processes...")
        for ps in self.procs.values():
            self._terminate(ps)
        time.sleep(1.0)
        for ps in self.procs.values():
            self._kill(ps)
        self._write_status()
        print("[ChatSupervisor] stopped.")

    def run(self) -> int:
        # Start all
        for ps in self.procs.values():
            self._start(ps)

        def _handle(sig, frame):
            self._running = False

        signal.signal(signal.SIGINT, _handle)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle)

        last_status_write = 0.0
        while self._running:
            now = time.time()

            # crash detection
            for name, ps in self.procs.items():
                p = ps.popen
                if p and p.poll() is not None:
                    self._restart(ps, reason=f"exit_code={p.returncode}")

            # staleness restart (optional)
            if self.args.restart_stale:
                sources = self._health_activity_sources()

                for svc in ("CM.ingestor", "CM.router_bank", "CM.emitter"):
                    ps = self.procs.get(svc)
                    if ps and self._is_stale(svc, sources.get(svc, []), self.args.stale_services):
                        self._restart(ps, reason=f"stale>{self.args.stale_services}s")

                for bot_id in list(self.worker_meta.keys()):
                    if self._worker_backlog_stale(bot_id, self.args.stale_workers):
                        for proc_name, ps in self.procs.items():
                            if proc_name.startswith(f"W.{bot_id}#"):
                                self._restart(ps, reason=f"backlog_stale>{self.args.stale_workers}s")

            # write status periodically
            if now - last_status_write >= self.args.status_every:
                self._write_status()
                last_status_write = now

            time.sleep(self.args.check_every)

        self.stop_all()
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Supervisor/Inspector for ChatManager + Workers.")
    ap.add_argument("--same-console", action="store_true", help="Run everything in same console.")
    ap.add_argument("--no-servers", action="store_true", help="Do not start http.server overlay servers.")
    ap.add_argument("--skip-writer", action="store_true", help="Skip SSNChatWriter.")
    ap.add_argument("--no-workers", action="store_true", help="Do not start worker bots.")

    ap.add_argument("--overlay-port", type=int, default=8080)
    ap.add_argument("--manager-port", type=int, default=8788)

    ap.add_argument("--restart-stale", action="store_true", help="Restart services/workers when they appear stuck.")
    ap.add_argument("--stale-services", type=float, default=45.0)
    ap.add_argument("--stale-workers", type=float, default=60.0)
    ap.add_argument("--check-every", type=float, default=0.5)
    ap.add_argument("--status-every", type=float, default=2.0)

    ap.add_argument("--allow-duplicate-inbox", action="store_true",
                    help="Allow multiple worker instances to read the same inbox (CAN duplicate processing).")

    args = ap.parse_args()

    bot_root = Path(__file__).resolve().parents[1]  # bot/
    sup = ChatSupervisor(bot_root, args)
    sup.build()
    return sup.run()


if __name__ == "__main__":
    raise SystemExit(main())
