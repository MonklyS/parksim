"""Park persistence in SQLite.

Each park gets a short public id (for share links) and a secret edit token returned once
at creation. Only a SHA-256 hash of the token is stored, so a leaked database can't be
used to edit parks. Anyone with the id can view a park; only the token holder can save it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS parks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    layout      TEXT NOT NULL,
    token_hash  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ParkStore:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute(SCHEMA)

    def create(self, layout: dict[str, Any]) -> tuple[str, str]:
        token = secrets.token_urlsafe(24)
        stamp = _now()
        with self._lock, self._db:
            while True:
                park_id = secrets.token_urlsafe(6)
                exists = self._db.execute("SELECT 1 FROM parks WHERE id = ?", (park_id,))
                if exists.fetchone() is None:
                    break
            self._db.execute(
                "INSERT INTO parks VALUES (?, ?, ?, ?, ?, ?)",
                (park_id, layout["name"], json.dumps(layout), _hash(token), stamp, stamp),
            )
        return park_id, token

    def get(self, park_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, layout, updated_at FROM parks WHERE id = ?", (park_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "layout": json.loads(row["layout"]),
            "updated_at": row["updated_at"],
        }

    def update(self, park_id: str, token: str, layout: dict[str, Any]) -> str:
        """Replace a park's layout. Raises KeyError (no such park) or PermissionError."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT token_hash FROM parks WHERE id = ?", (park_id,)
            ).fetchone()
            if row is None:
                raise KeyError(park_id)
            if not hmac.compare_digest(row["token_hash"], _hash(token)):
                raise PermissionError(park_id)
            stamp = _now()
            self._db.execute(
                "UPDATE parks SET name = ?, layout = ?, updated_at = ? WHERE id = ?",
                (layout["name"], json.dumps(layout), stamp, park_id),
            )
        return stamp

    def close(self) -> None:
        with self._lock:
            self._db.close()
