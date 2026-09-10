from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EvidenceRecorder:
    def __init__(self, root: Path, secrets: list[str] | None = None) -> None:
        """Create a timestamped run below ``root`` and remember values to redact."""
        self.evidence_id = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        self.run_dir = root / "runs" / self.evidence_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "events.jsonl"
        self._secrets = [value for value in (secrets or []) if value]

    def redact(self, value: Any) -> Any:
        """Return ``value`` recursively scrubbed of configured secrets and bearer tokens."""
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if not isinstance(value, str):
            return value
        redacted = value
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1[REDACTED]", redacted)
        return redacted

    def record(self, event: str, **data: Any) -> None:
        """Append one timestamped, redacted ``event`` and its fields to JSONL evidence."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **self.redact(data),
        }
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")


class NullEvidenceRecorder(EvidenceRecorder):
    def __init__(self) -> None:
        """Create a no-op recorder with a stable placeholder evidence identifier."""
        self.evidence_id = "none"

    def record(self, event: str, **data: Any) -> None:
        """Discard ``event`` and ``data`` instead of persisting evidence."""
        return None
