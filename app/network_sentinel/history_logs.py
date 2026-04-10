from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LOG_FILE_RE = re.compile(r"^network_log_(?P<service_id>[0-9a-fA-F-]+)_(?P<day>\d{4}-\d{2}-\d{2})\.txt$")


@dataclass(frozen=True)
class ServiceLogPaths:
    base_dir: Path

    def ensure(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def file_for(self, service_id: str, dt: datetime) -> Path:
        day = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        # Per-service-per-day keeps the exact line format unchanged.
        return self.base_dir / f"network_log_{service_id}_{day}.txt"


def default_log_dir(project_root: Path) -> Path:
    # Match legacy expectation: a "logs" folder at runtime root.
    # We nest under logs/network for cleanliness.
    return project_root / "logs" / "network"


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + os.linesep)


def prune_old_logs(base_dir: Path, keep_days: int, now: datetime | None = None) -> int:
    if keep_days < 1 or not base_dir.exists():
        return 0

    deleted = 0
    cutoff_day = (now or datetime.now(timezone.utc)).date() - timedelta(days=keep_days)
    for path in base_dir.glob("network_log_*_*.txt"):
        match = _LOG_FILE_RE.match(path.name)
        if not match:
            continue
        try:
            log_day = datetime.strptime(match.group("day"), "%Y-%m-%d").date()
        except ValueError:
            continue
        if log_day < cutoff_day:
            try:
                path.unlink(missing_ok=True)
                deleted += 1
            except Exception:
                continue
    return deleted

