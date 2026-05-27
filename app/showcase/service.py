import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app import service as _svc_module

from .archive import ArchiveWriter
from .openrouter_image_client import OpenRouterImageClient, OpenRouterImageClientError
from .prompt import build_showcase_prompt
from .storage import StorageManager

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - python < 3.9
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]


logger = logging.getLogger(__name__)


def _resolve_timezone(timezone_name: str):
    if ZoneInfo is None:
        return timezone(timedelta(hours=8))
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8))


class ShowcaseService:
    """Orchestrates a single image-to-image showcase generation request."""

    def __init__(
        self,
        *,
        model: str,
        storage_manager: StorageManager,
        archive_writer: ArchiveWriter,
        client: OpenRouterImageClient,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.model = model
        self.storage_manager = storage_manager
        self.archive_writer = archive_writer
        self.client = client
        self.timezone_name = timezone_name

    def generate_showcase(
        self,
        *,
        upload_filename: str,
        content_type: str,
        image_bytes: bytes,
        prompt_hint: Optional[str],
        model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        now = datetime.now(_resolve_timezone(self.timezone_name))
        from app.observability import context as obs_ctx
        existing = obs_ctx.get_request_id()
        request_id = existing or f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        override = (model_override or "").strip() or None
        effective_model = override or self.model

        input_path = self.storage_manager.save_input_image(
            request_id=request_id,
            image_bytes=image_bytes,
            original_filename=upload_filename,
            now=now,
        )
        final_prompt = build_showcase_prompt(prompt_hint)

        try:
            result = self.client.generate_image(
                prompt=final_prompt,
                image_bytes=image_bytes,
                content_type=content_type,
                request_id=request_id,
                model=override,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            error_code = "upstream_generation_failed"
            status_code = None
            if isinstance(exc, OpenRouterImageClientError):
                error_code = exc.error_code
                status_code = exc.status_code

            response = {
                "request_id": request_id,
                "status": "failed",
                "model": effective_model,
                "model_override": override,
                "error_code": error_code,
                "error_message": str(exc),
                "latency_ms": latency_ms,
                "created_at": now.isoformat(),
            }
            self.archive_writer.write_record(
                request_id=request_id,
                record={
                    "request_id": request_id,
                    "created_at": response["created_at"],
                    "status": "failed",
                    "model": effective_model,
                    "model_override": override,
                    "prompt_hint": prompt_hint,
                    "final_prompt": final_prompt,
                    "input_path": input_path.as_posix() if input_path else None,
                    "output_path": None,
                    "latency_ms": latency_ms,
                    "retry_count": None,
                    "content_type": content_type,
                    "file_size": len(image_bytes),
                    "upstream_status_code": status_code,
                    "response_parse_status": "failed",
                    "error_code": error_code,
                    "error_message": str(exc),
                },
                now=now,
            )
            logger.exception("Showcase generation failed for request_id=%s", request_id)
            if _svc_module.recorder is not None:
                try:
                    _svc_module.recorder.record_llm_stage(
                        request_id=request_id,
                        stage="showcase_generate",
                        attempts=[],
                        messages=[{"role": "user", "content": final_prompt}],
                        raw_response=None,
                        parsed=None,
                    )
                except Exception:
                    pass
            return response

        output_path = self.storage_manager.save_output_image(
            request_id=request_id,
            mime_type=result.image.mime_type,
            base64_data=result.image.base64_data,
            now=now,
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        response = {
            "request_id": request_id,
            "status": "succeeded",
            "model": effective_model,
            "model_override": override,
            "prompt_hint": prompt_hint,
            "final_prompt": final_prompt,
            "image_base64": result.image.base64_data,
            "image_mime_type": result.image.mime_type,
            "input_path": input_path.as_posix() if input_path else None,
            "output_path": output_path.as_posix() if output_path else None,
            "latency_ms": latency_ms,
            "created_at": now.isoformat(),
        }
        self.archive_writer.write_record(
            request_id=request_id,
            record={
                "request_id": request_id,
                "created_at": response["created_at"],
                "status": "succeeded",
                "model": effective_model,
                "model_override": override,
                "prompt_hint": prompt_hint,
                "final_prompt": final_prompt,
                "input_path": response["input_path"],
                "output_path": response["output_path"],
                "latency_ms": latency_ms,
                "retry_count": result.attempts,
                "content_type": content_type,
                "file_size": len(image_bytes),
                "upstream_status_code": result.upstream_status_code,
                "response_parse_status": "ok",
                "error_code": None,
                "error_message": None,
            },
            now=now,
        )
        logger.info("Showcase generation succeeded for request_id=%s", request_id)
        if _svc_module.recorder is not None:
            try:
                _svc_module.recorder.record_llm_stage(
                    request_id=request_id,
                    stage="showcase_generate",
                    attempts=[a.__dict__ for a in result.attempt_records],
                    messages=[{"role": "user", "content": final_prompt}],
                    raw_response=result.response_body,
                    parsed=None,
                )
            except Exception:
                pass
        return response
