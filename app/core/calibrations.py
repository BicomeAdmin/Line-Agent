from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.storage.paths import calibrations_state_path


@dataclass
class CalibrationRecord:
    customer_id: str
    community_id: str
    input_x: int
    input_y: int
    send_x: int
    send_y: int
    source: str = "runtime_cli"
    note: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def key(self) -> str:
        return f"{self.customer_id}:{self.community_id}"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CalibrationStore:
    def __init__(self, state_path: Path | None = None, persist: bool = True) -> None:
        self._state_path = state_path or calibrations_state_path()
        self._persist_enabled = persist
        self._lock = threading.Lock()
        self._records: dict[str, CalibrationRecord] = {}
        if self._persist_enabled:
            self._load()

    def upsert(self, record: CalibrationRecord) -> CalibrationRecord:
        with self._lock:
            existing = self._records.get(record.key())
            if existing is not None:
                record.created_at = existing.created_at
            record.updated_at = time.time()
            self._records[record.key()] = record
            self._persist(record)
            return record

    def get(self, customer_id: str, community_id: str) -> CalibrationRecord | None:
        with self._lock:
            return self._records.get(f"{customer_id}:{community_id}")

    def list_all(self) -> list[CalibrationRecord]:
        with self._lock:
            return sorted(self._records.values(), key=lambda item: item.updated_at, reverse=True)

    def _persist(self, record: CalibrationRecord) -> None:
        if not self._persist_enabled:
            return
        with self._state_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        latest: dict[str, CalibrationRecord] = {}
        for raw_line in self._state_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            record = CalibrationRecord(
                customer_id=str(payload["customer_id"]),
                community_id=str(payload["community_id"]),
                input_x=int(payload["input_x"]),
                input_y=int(payload["input_y"]),
                send_x=int(payload["send_x"]),
                send_y=int(payload["send_y"]),
                source=str(payload.get("source", "runtime_cli")),
                note=str(payload["note"]) if isinstance(payload.get("note"), str) else None,
                created_at=float(payload.get("created_at", time.time())),
                updated_at=float(payload.get("updated_at", time.time())),
            )
            latest[record.key()] = record
        self._records = latest


calibration_store = CalibrationStore()
