import os
import json
from typing import Any, Dict, List, Tuple


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    _ensure_dir(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_jsonl_since(path: str, offset: int) -> Tuple[List[Dict[str, Any]], int]:
    """
    Read JSONL records since byte offset. Returns (records, new_offset).
    Safe for append-only logs.
    """
    if not os.path.exists(path):
        return [], offset

    recs: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                # ignore malformed lines
                continue
        return recs, f.tell()
