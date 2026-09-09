from __future__ import annotations

import json
from pathlib import Path

import yaml

from computer_use.models import CapabilityArtifact


def load_artifact(path: Path) -> CapabilityArtifact:
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) if path.suffix in {".yaml", ".yml"} else json.loads(raw)
    return CapabilityArtifact.model_validate(data)


def save_artifact(artifact: CapabilityArtifact, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = artifact.model_dump(mode="json", exclude_none=True)
    if path.suffix in {".yaml", ".yml"}:
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

