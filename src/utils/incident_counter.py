"""
Incident Counter for Amaze on Work.

Provides auto-incrementing INC-XXXX numbers persisted to disk.
Thread-safe via file locking.
"""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# Default storage path — sits at project root
_DEFAULT_STORE = Path(__file__).parent.parent.parent / "incidents.json"


def _load(store_path: Path) -> dict:
    if store_path.exists():
        try:
            with open(store_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"counter": 0, "incidents": {}}


def _save(data: dict, store_path: Path) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class IncidentCounter:
    """
    Auto-incrementing incident ID generator.

    IDs are formatted as INC-XXXX (e.g., INC-0001, INC-0042).
    Counter persists across restarts via a JSON file.
    """

    def __init__(self, store_path: Path | None = None):
        self._store = Path(store_path) if store_path else _DEFAULT_STORE

    def next_id(self, metadata: dict | None = None) -> str:
        """
        Assign and return the next incident ID.

        Args:
            metadata: Optional dict to persist along with the incident
                      (e.g., {"source": "slack", "channel": "C0AL..."})

        Returns:
            str: e.g. "INC-0042"
        """
        with _lock:
            data = _load(self._store)
            data["counter"] = data.get("counter", 0) + 1
            inc_id = f"INC-{data['counter']:04d}"
            if metadata:
                data.setdefault("incidents", {})[inc_id] = metadata
            _save(data, self._store)
            logger.info(f"Assigned incident ID: {inc_id}")
            return inc_id

    def peek(self) -> int:
        """Return current counter value without incrementing."""
        with _lock:
            return _load(self._store).get("counter", 0)

    def get_metadata(self, inc_id: str) -> dict:
        """Retrieve stored metadata for an incident ID."""
        with _lock:
            data = _load(self._store)
            return data.get("incidents", {}).get(inc_id, {})

    def record(self, inc_id: str, key: str, value) -> None:
        """Update stored metadata for an existing incident."""
        with _lock:
            data = _load(self._store)
            data.setdefault("incidents", {}).setdefault(inc_id, {})[key] = value
            _save(data, self._store)


# Module-level singleton for convenience
_default_counter = IncidentCounter()


def next_incident_id(metadata: dict | None = None) -> str:
    """Convenience function — returns next INC-XXXX using default store."""
    return _default_counter.next_id(metadata)
