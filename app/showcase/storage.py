import base64
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Optional, Union


class StorageManager:
    """Persists the input/output image bytes when retention is enabled."""

    def __init__(
        self,
        storage_root: Union[str, Path],
        *,
        retain_input_files: bool,
        retain_output_files: bool,
    ) -> None:
        self.storage_root = Path(storage_root)
        self.retain_input_files = retain_input_files
        self.retain_output_files = retain_output_files
        self.storage_root.mkdir(parents=True, exist_ok=True)

    def save_input_image(
        self,
        *,
        request_id: str,
        image_bytes: bytes,
        original_filename: str,
        now: datetime,
    ) -> Optional[Path]:
        if not self.retain_input_files:
            return None
        suffix = Path(original_filename).suffix or ".bin"
        path = self.storage_root / "inputs" / now.strftime("%Y%m%d") / f"{request_id}_input{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)
        return path

    def save_output_image(
        self,
        *,
        request_id: str,
        mime_type: str,
        base64_data: str,
        now: datetime,
    ) -> Optional[Path]:
        if not self.retain_output_files:
            return None
        extension = mimetypes.guess_extension(mime_type) or ".png"
        path = self.storage_root / "outputs" / now.strftime("%Y%m%d") / f"{request_id}_output{extension}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(base64_data))
        return path
