"""Crash-safe progress + dead-letter for batch jobs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.integrations.batch_llm.limits import defaults


@dataclass
class DeadLetterEntry:
    item_id: str
    error: str
    attempts: int
    payload: Any = None


class CheckpointStore:
    def __init__(self, checkpoint_key: str, *, base_dir: str | None = None) -> None:
        cfg = defaults()
        root = Path(base_dir or cfg.get("checkpoint_dir") or "data/batch_llm_checkpoints")
        root.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in checkpoint_key)[:120]
        self.path = root / f"{safe}.sqlite3"
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                item_id TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dead_letter (
                item_id TEXT PRIMARY KEY,
                error TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                payload_json TEXT,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def completed_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT item_id FROM results").fetchall()
        return {r[0] for r in rows}

    def get_result(self, item_id: str) -> Any | None:
        row = self._conn.execute(
            "SELECT result_json FROM results WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def save_result(self, item_id: str, result: Any) -> None:
        import time

        self._conn.execute(
            """
            INSERT INTO results(item_id, result_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
              result_json=excluded.result_json,
              updated_at=excluded.updated_at
            """,
            (item_id, json.dumps(result), time.time()),
        )
        self._conn.commit()

    def save_dead_letter(self, entry: DeadLetterEntry) -> None:
        import time

        self._conn.execute(
            """
            INSERT INTO dead_letter(item_id, error, attempts, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
              error=excluded.error,
              attempts=excluded.attempts,
              payload_json=excluded.payload_json,
              updated_at=excluded.updated_at
            """,
            (
                entry.item_id,
                entry.error,
                entry.attempts,
                json.dumps(entry.payload) if entry.payload is not None else None,
                time.time(),
            ),
        )
        self._conn.commit()

    def dead_letters(self) -> list[DeadLetterEntry]:
        rows = self._conn.execute(
            "SELECT item_id, error, attempts, payload_json FROM dead_letter"
        ).fetchall()
        out: list[DeadLetterEntry] = []
        for item_id, error, attempts, payload_json in rows:
            payload = json.loads(payload_json) if payload_json else None
            out.append(
                DeadLetterEntry(
                    item_id=item_id, error=error, attempts=attempts, payload=payload
                )
            )
        return out

    def all_results(self) -> dict[str, Any]:
        rows = self._conn.execute("SELECT item_id, result_json FROM results").fetchall()
        return {item_id: json.loads(blob) for item_id, blob in rows}
