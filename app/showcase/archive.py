import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Union


class ArchiveWriter:
    """Writes one JSON archive record per showcase request under logs/showcase/."""

    def __init__(self, archive_root: Union[str, Path]) -> None:
        self.archive_root = Path(archive_root)
        self.archive_root.mkdir(parents=True, exist_ok=True)

    def write_record(
        self,
        *,
        request_id: str,
        record: Dict[str, Any],
        now: datetime,
    ) -> Path:
        path = self.archive_root / now.strftime("%Y%m%d") / f"{request_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
