from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


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

