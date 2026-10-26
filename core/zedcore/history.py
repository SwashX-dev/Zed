"""Append-only record of system changes, shared by every Zed tool."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"time": datetime.now(tz=UTC).isoformat(), **record}
    line = json.dumps(record, sort_keys=True) + "\n"
    # O_APPEND writes of one short line are atomic with respect to other
    # appenders; fsync so the record survives a crash during activation.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path: Path, limit: int = 100) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
