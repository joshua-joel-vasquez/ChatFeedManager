import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional


_LEVEL_MAP = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _resolve_log_dir(base_dir: Path, cfg: Dict[str, Any]) -> Path:
    log_cfg = (cfg or {}).get("logging") or {}
    d = str(log_cfg.get("dir") or "../logs").strip()
    if not d:
        d = "../logs"

    p = Path(os.path.expanduser(d))
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return p


def _level_from_cfg(cfg: Dict[str, Any]) -> int:
    log_cfg = (cfg or {}).get("logging") or {}
    lvl = str(log_cfg.get("level") or "INFO").strip().upper()
    return _LEVEL_MAP.get(lvl, logging.INFO)


def setup_logging(service_name: str, cfg: Dict[str, Any], base_dir: Optional[Path] = None) -> logging.Logger:
    """Configure logging for a service.

    - Per-service rotating log: <service>.<YYYY-MM-DD>.log
    - Shared rotating log: latest.log ("symlink-like" quick view)
    - Console output

    The logging directory + level are controlled via commands.txt:

      "logging": { "dir": "../logs", "level": "DEBUG" }

    `base_dir` should be the ChatManager directory.
    """

    if base_dir is None:
        base_dir = Path(__file__).resolve().parents[1]

    logs_dir = _resolve_log_dir(base_dir, cfg)
    logs_dir.mkdir(parents=True, exist_ok=True)

    level = _level_from_cfg(cfg)
    log_cfg = (cfg or {}).get("logging") or {}
    max_bytes = int(log_cfg.get("max_bytes") or 5 * 1024 * 1024)
    backup_count = int(log_cfg.get("backup_count") or 5)

    date_str = time.strftime("%Y-%m-%d")
    service_file = logs_dir / f"{service_name}.{date_str}.log"
    latest_file = logs_dir / "latest.log"

    logger = logging.getLogger(service_name)
    logger.setLevel(level)
    logger.propagate = False

    # Idempotent: clear old handlers
    for h in list(logger.handlers):
        try:
            logger.removeHandler(h)
            h.close()
        except Exception:
            pass

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Per-service rotating file
    fh = RotatingFileHandler(
        service_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # "latest.log" rotating file (combined across services)
    lh = RotatingFileHandler(
        latest_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    lh.setLevel(level)
    lh.setFormatter(fmt)
    logger.addHandler(lh)

    _install_global_exception_hooks(logger)

    logger.debug(
        "Logging initialized: level=%s logs_dir=%s service_file=%s",
        logging.getLevelName(level),
        str(logs_dir),
        str(service_file),
    )

    return logger


def _install_global_exception_hooks(logger: logging.Logger) -> None:
    # sys.excepthook
    def _excepthook(exctype, value, tb):
        try:
            logger.critical("Unhandled exception", exc_info=(exctype, value, tb))
        except Exception:
            pass
        # Also print default behavior
        try:
            sys.__excepthook__(exctype, value, tb)
        except Exception:
            pass

    sys.excepthook = _excepthook

    # threading.excepthook (Python 3.8+)
    try:
        import threading

        def _thread_excepthook(args):
            try:
                logger.critical(
                    "Unhandled thread exception in %s",
                    getattr(args, "thread", None).name if getattr(args, "thread", None) else "(unknown)",
                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                )
            except Exception:
                pass

        if hasattr(threading, "excepthook"):
            threading.excepthook = _thread_excepthook  # type: ignore[attr-defined]
    except Exception:
        pass
