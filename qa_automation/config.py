from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_local_env() -> None:
    """Load simple KEY=VALUE settings without adding a credential dependency."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(key: str, default: str = "") -> str:
    """อ่านค่าจาก .env/environment (URL ของระบบภายใน, Sheet ID) — ไม่เก็บค่าจริงไว้ในโค้ด."""
    _load_local_env()
    return os.getenv(key, default).strip().rstrip("/")
