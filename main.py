import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.config import BASE_DIR, load_settings
from app.constants import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.data.brands import BrandStore
from app.data.categories import CategoryStore
from app.errors import BadRequestError, LLMAllAttemptsFailedError
from app.image_processing import compress_image_if_needed
from app.jobs import AnalysisJobStore
from app.llm.client import OpenRouterClient
from app.request_logging import build_request_log, write_request_log
from app.runtime_config import get_public_config, update_runtime_config
from app.service import MercariAnalyzer
from app.showcase.archive import ArchiveWriter as ShowcaseArchiveWriter
from app.showcase.openrouter_image_client import OpenRouterImageClient
from app.showcase.service import ShowcaseService
from app.showcase.storage import StorageManager as ShowcaseStorageManager
from app.utils import parse_bool_param

settings = load_settings()
brand_store = BrandStore(settings.brand_csv_path)
category_store = CategoryStore(settings.category_csv_path)
vision_client = OpenRouterClient(
    api_key=settings.openrouter_api_key,
    base_url=settings.openrouter_base_url,
    timeout=settings.request_timeout,
    referer=settings.openrouter_referer,
    app_name=settings.openrouter_app_name,
    reasoning=settings.reasoning,
)
category_client = OpenRouterClient(
    api_key=settings.openrouter_api_key,
    base_url=settings.openrouter_base_url,
    timeout=settings.request_timeout,
    referer=settings.openrouter_referer,
    app_name=settings.openrouter_app_name,
    reasoning=settings.reasoning,
)

analyzer = MercariAnalyzer(
    settings=settings,
    brand_store=brand_store,
    category_store=category_store,
    vision_client=vision_client,
    category_client=category_client,
)
product_data_executor = ThreadPoolExecutor(max_workers=4)
analysis_job_store = AnalysisJobStore()


def _resolve_showcase_path(value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else BASE_DIR / candidate


showcase_storage_manager = ShowcaseStorageManager(
    _resolve_showcase_path(settings.showcase_storage_root),
    retain_input_files=settings.showcase_retain_input_files,
    retain_output_files=settings.showcase_retain_output_files,
)
showcase_archive_writer = ShowcaseArchiveWriter(BASE_DIR / "logs" / "showcase")
showcase_image_client = OpenRouterImageClient(
    api_key=settings.openrouter_api_key,
    base_url=settings.openrouter_base_url,
    model=settings.showcase_model,
    timeout=settings.showcase_request_timeout,
    max_retries=settings.showcase_max_retries,
    referer=settings.openrouter_referer,
    app_name=settings.openrouter_app_name,
    fallback_models=settings.showcase_fallback_models,
)
showcase_service = ShowcaseService(
    model=settings.showcase_model,
    storage_manager=showcase_storage_manager,
    archive_writer=showcase_archive_writer,
    client=showcase_image_client,
    timezone_name=settings.showcase_timezone,
)

def _format_attempts_error(exc: LLMAllAttemptsFailedError) -> Dict[str, Any]:
    return {
        "message": f"{exc.stage} stage failed after {len(exc.attempts)} attempt(s).",
        "stage": exc.stage,
        "kind": "all_attempts_failed",
        "attempts": [
            {
                "model": a.model,
                "attempt": a.attempt,
                "attempt_global": a.attempt_global,
                "error_kind": a.error_kind,
                "message": a.message,
                "status_code": a.status_code,
                "latency_ms": a.latency_ms,
            }
            for a in exc.attempts
        ],
    }


app = FastAPI(title="Mercari Image Analyzer", version="1.0.0")
CONFIG_ENV_PATH = BASE_DIR / ".env"
CONFIG_PAGE_PATH = BASE_DIR / "web" / "config.html"

# Allow local dev CORS for the test page or other origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sync_runtime_clients() -> None:
    vision_client.timeout = settings.request_timeout
    category_client.timeout = settings.request_timeout
    showcase_image_client.model = settings.showcase_model
    showcase_image_client.fallback_models = list(settings.showcase_fallback_models or [])
    showcase_service.model = settings.showcase_model


def _merge_analysis_payload(
    classification: Dict[str, Any],
    product_data: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(classification)
    payload.update(product_data)
    if classification.get("image_processing") and "image_processing" not in product_data:
        payload["image_processing"] = classification["image_processing"]
    timings = dict(classification.get("timings") or {})
    timings.update(product_data.get("timings") or {})
    classification_ms = timings.get("classification_ms")
    product_data_ms = timings.get("product_data_ms")
    if isinstance(classification_ms, (int, float)) and isinstance(product_data_ms, (int, float)):
        timings["total_ms"] = round(max(float(classification_ms), float(product_data_ms)), 2)
    payload["timings"] = timings
    if isinstance(classification.get("_debug"), dict) or isinstance(product_data.get("_debug"), dict):
        debug_payload: Dict[str, Any] = {}
        attempts: Dict[str, Any] = {}
        if isinstance(classification.get("_debug"), dict):
            debug_payload.update(classification["_debug"])
            if isinstance(classification["_debug"].get("attempts"), dict):
                attempts.update(classification["_debug"]["attempts"])
        if isinstance(product_data.get("_debug"), dict):
            debug_payload.update(product_data["_debug"])
            if isinstance(product_data["_debug"].get("attempts"), dict):
                attempts.update(product_data["_debug"]["attempts"])
        if attempts:
            debug_payload["attempts"] = attempts
        payload["_debug"] = debug_payload
    payload["status"] = "completed"
    return payload


def _pending_payload(job_id: str, classification: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(classification)
    payload["job_id"] = job_id
    payload["status"] = "product_pending"
    return payload


def _resolve_product_source(
    job: Dict[str, Any],
    *,
    raise_product_errors: bool,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[BaseException]]:
    """Decide which product-data future (if any) should be used right now.

    Returns ``(source, product_data, error)`` where:
      * ``source`` is ``"primary"`` / ``"fallback"`` / ``None``;
      * ``product_data`` is the parsed result dict when ``source`` is set;
      * ``error`` carries an exception that should be raised by the caller
        when both paths failed (only when ``raise_product_errors`` is True).
    """
    primary = job["future"]
    fallback = job.get("fallback_future")
    started_at = job.get("started_at")
    fallback_timeout = job.get("fallback_timeout")

    primary_done = primary.done()
    primary_error = primary.exception() if primary_done else None
    primary_ok = primary_done and primary_error is None

    fallback_done = bool(fallback and fallback.done())
    fallback_error = fallback.exception() if fallback_done else None
    fallback_ok = fallback_done and fallback_error is None

    if primary_ok:
        return "primary", primary.result(), None

    timed_out = False
    if (
        fallback is not None
        and started_at is not None
        and fallback_timeout is not None
    ):
        timed_out = (time.monotonic() - float(started_at)) >= float(fallback_timeout)

    if fallback_ok and (primary_done or timed_out):
        return "fallback", fallback.result(), None

    if primary_done and primary_error is not None:
        if fallback is None or fallback_done:
            if raise_product_errors:
                return None, None, primary_error
            return None, None, None

    return None, None, None


def _job_payload(
    job_id: str,
    classification: Dict[str, Any],
    future,
    *,
    raise_product_errors: bool = True,
    fallback_future=None,
    started_at: Optional[float] = None,
    fallback_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    job = {
        "future": future,
        "fallback_future": fallback_future,
        "started_at": started_at,
        "fallback_timeout": fallback_timeout,
    }
    source, product_data, error = _resolve_product_source(
        job,
        raise_product_errors=raise_product_errors,
    )
    if error is not None:
        raise error
    if source is None or product_data is None:
        return _pending_payload(job_id, classification)
    payload = _merge_analysis_payload(classification, product_data)
    payload["job_id"] = job_id
    payload["product_data_source"] = source
    return payload


@app.get("/config", response_class=HTMLResponse)
def config_page() -> HTMLResponse:
    try:
        return HTMLResponse(CONFIG_PAGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Config page not found.") from exc


@app.get("/api/v1/config")
def read_config() -> Dict[str, Any]:
    return get_public_config(settings)


@app.put("/api/v1/config")
def save_config(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlparse(origin).netloc
        request_host = request.headers.get("host", "")
        if origin_host != request_host:
            raise HTTPException(status_code=403, detail="Cross-origin config updates are not allowed.")
    try:
        return update_runtime_config(
            settings,
            payload,
            env_path=CONFIG_ENV_PATH,
            on_applied=_sync_runtime_clients,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.middleware("http")
async def log_requests(request: Request, call_next):
    if not settings.log_requests:
        return await call_next(request)
    start = time.monotonic()
    response = None
    error_message = ""
    status_code = 500
    body = b""
    try:
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()

            async def receive() -> dict:
                return {"type": "http.request", "body": body, "more_body": False}

            request = Request(request.scope, receive)
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:
        error_message = str(exc)
        raise
    finally:
        duration_ms = (time.monotonic() - start) * 1000
        try:
            entry = await build_request_log(request, body=body)
            write_request_log(
                entry,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error_message,
                retention_days=settings.log_requests_retention_days,
                max_files=settings.log_requests_max_files,
            )
        except Exception:
            pass


@app.post("/api/v1/mercari/image/analyze")
async def analyze_image(
    image_list: List[UploadFile] = File(...),
    language: str = Form(DEFAULT_LANGUAGE),
    debug: str = Form("false"),
    category_count: int = Form(3),
    vision_model: str = Form(None),
    category_model: str = Form(None),
):
    if not image_list:
        raise HTTPException(status_code=400, detail="Image files are required.")

    image_list = image_list
    image_payloads: List[Tuple[bytes, str]] = []
    image_processing = []
    for index, image in enumerate(image_list, start=1):
        if not image:
            raise HTTPException(
                status_code=400, detail=f"Image file is required (index {index})."
            )

        if image.content_type not in settings.allowed_mime_types:
            raise HTTPException(
                status_code=400, detail=f"Unsupported image type (index {index})."
            )

        try:
            data = await image.read()
        except Exception:
            raise HTTPException(
                status_code=400, detail=f"Failed to read uploaded file (index {index})."
            )

        if not data:
            raise HTTPException(
                status_code=400, detail=f"Uploaded image is empty (index {index})."
            )

        if len(data) > settings.max_image_bytes:
            raise HTTPException(
                status_code=400, detail=f"Image is too large (index {index})."
            )

        processed = compress_image_if_needed(
            image_bytes=data,
            mime_type=image.content_type or "application/octet-stream",
            threshold_bytes=settings.image_compression_threshold_bytes,
        )
        image_payloads.append((processed.data, processed.mime_type))
        image_processing.append(
            {
                "index": index,
                "filename": image.filename or "",
                "compressed": processed.compressed,
                "original_bytes": processed.original_bytes,
                "processed_bytes": processed.processed_bytes,
            }
        )

    language = language or DEFAULT_LANGUAGE
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Invalid language.")

    debug_enabled = settings.enable_debug_param and parse_bool_param(debug, False)
    category_count = max(1, min(category_count, 3))

    try:
        job_id = uuid.uuid4().hex
        product_future = product_data_executor.submit(
            analyzer.generate_product_data,
            images=image_payloads,
            language=language,
            debug=debug_enabled,
            use_fallback_prompt=False,
        )
        fallback_model = (settings.product_data_fallback_model or "").strip()
        fallback_future = None
        if fallback_model:
            fallback_future = product_data_executor.submit(
                analyzer.generate_product_data,
                images=image_payloads,
                language=language,
                debug=debug_enabled,
                model_override=fallback_model,
                use_fallback_prompt=True,
            )
        fallback_timeout = float(settings.product_data_fallback_timeout_seconds)
        classification = await run_in_threadpool(
            analyzer.classify_first_image_categories,
            images=image_payloads,
            language=language,
            debug=debug_enabled,
            category_limit=category_count,
            vision_model_override=vision_model,
            category_model_override=category_model,
            image_processing=image_processing,
        )
        # Start the fallback timer AFTER classification finishes. The primary
        # and fallback futures begin running as soon as they are submitted,
        # but the user only starts polling after they receive the initial
        # response (which is gated on classification). Measuring the timeout
        # from before classification would consume the timeout budget while
        # the user is still waiting for the first response, causing the
        # fallback to win prematurely.
        fallback_started_at = time.monotonic()
        analysis_job_store.put(
            job_id,
            classification=classification,
            future=product_future,
            fallback_future=fallback_future,
            started_at=fallback_started_at,
            fallback_timeout=fallback_timeout,
        )
        result = _job_payload(
            job_id,
            classification,
            product_future,
            raise_product_errors=False,
            fallback_future=fallback_future,
            started_at=fallback_started_at,
            fallback_timeout=fallback_timeout,
        )
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMAllAttemptsFailedError as exc:
        raise HTTPException(status_code=502, detail=_format_attempts_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc

    return JSONResponse(result)


@app.get("/api/v1/mercari/image/analyze/{job_id}")
async def poll_image_analysis(job_id: str):
    job = analysis_job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    try:
        result = await run_in_threadpool(
            _job_payload,
            job_id,
            job["classification"],
            job["future"],
            fallback_future=job.get("fallback_future"),
            started_at=job.get("started_at"),
            fallback_timeout=job.get("fallback_timeout"),
        )
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMAllAttemptsFailedError as exc:
        raise HTTPException(status_code=502, detail=_format_attempts_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
    return JSONResponse(result)


class TitleCategoryRequest(BaseModel):
    title: str
    image_url: Optional[str] = None
    language: Optional[str] = DEFAULT_LANGUAGE


@app.post("/api/v1/mercari/title/analyze")
async def analyze_title(request: TitleCategoryRequest):
    language = request.language or DEFAULT_LANGUAGE
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Invalid language.")

    try:
        result = await run_in_threadpool(
            analyzer.analyze_title,
            title=request.title,
            image_url=request.image_url,
            language=language,
        )
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMAllAttemptsFailedError as exc:
        raise HTTPException(status_code=502, detail=_format_attempts_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc

    return JSONResponse(result)


@app.post("/api/v1/showcase/generate")
async def generate_showcase(
    file: UploadFile = File(...),
    prompt_hint: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image uploads are supported.")

    try:
        image_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file.")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    payload = await run_in_threadpool(
        showcase_service.generate_showcase,
        upload_filename=file.filename or "upload.bin",
        content_type=file.content_type,
        image_bytes=image_bytes,
        prompt_hint=prompt_hint,
        model_override=model,
    )
    status_code = 200 if payload.get("status") == "succeeded" else 502
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "models": {
            "vision_model": settings.vision_model,
            "category_model": settings.category_model,
            "showcase_model": settings.showcase_model,
        },
    }
