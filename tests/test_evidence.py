import re
from pathlib import Path

from computer_use.evidence import EvidenceRecorder


def test_run_directory_uses_utc_timestamp(tmp_path: Path) -> None:
    recorder = EvidenceRecorder(tmp_path)

    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.\d{6}Z",
        recorder.evidence_id,
    )
    assert recorder.run_dir == tmp_path / "runs" / recorder.evidence_id
    assert recorder.run_dir.is_dir()
