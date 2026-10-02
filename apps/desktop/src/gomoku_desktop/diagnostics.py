"""GUI boundary logs; neural network implementation is deliberately untouched."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading

_lock = threading.Lock()
_root = Path(__file__).resolve().parents[4]


def log(event, *, level="info", **fields):
    configured = os.environ.get("GOMOKU_LOG_LEVEL", "info")
    if configured == "off" or (level == "debug" and configured != "debug"):
        return
    try:
        now = datetime.now(timezone.utc)
        record = {**fields, "timestampMs": int(now.timestamp() * 1000),
                  "component": "desktop-gui", "level": level, "event": event}
        directory = Path(os.environ.get("GOMOKU_LOG_DIR", _root / "logs"))
        with _lock:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / f"gui-desktop-{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError, TypeError):
        pass
