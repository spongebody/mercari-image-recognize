import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.datastructures import UploadFile as _Upload
from starlette.requests import Request as _SReq
from starlette.responses import Response as _Resp

from app.config import BASE_DIR, load_settings
from app.constants import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.data.brands import BrandStore
from app.data.categories import CategoryStore
from app.errors import BadRequestError, LLMAllAttemptsFailedError
from app.image_processing import compress_image_if_needed
from app.jobs import AnalysisJobStore
from app.llm.client import OpenRouterClient
from app.observability import context as obs_ctx
from app.observability.api import build_router as build_obs_router
from app.observability.auth import require_logs_auth
from app.observability.recorder import Recorder
from app.observability.retention import prune as obs_prune
from app.observability.store import Store as ObsStore
from app.runtime_config import get_public_config, update_runtime_config
from app import service as _svc_module
from app.service import MercariAnalyzer
from app.showcase.archive import ArchiveWriter as ShowcaseArchiveWriter
from app.showcase.openrouter_image_client import OpenRouterImageClient
from app.showcase.service import ShowcaseService
from app.showcase.storage import StorageManager as ShowcaseStorageManager
from app.utils import parse_bool_param

settings = load_settings()
_obs_store = ObsStore(BASE_DIR / "logs" / "observability.db")
_obs_store.init_schema()
recorder = Recorder(store=_obs_store, store_root=BASE_DIR / "logs" / "store")
_svc_module.set_recorder(recorder)
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


def _submit_with_request_id(fn, /, *args, **kwargs):
    rid = obs_ctx.get_request_id()
    def _runner():
        token = obs_ctx.set_request_id(rid) if rid else None
        try:
            return fn(*args, **kwargs)
        finally:
            if token is not None:
                obs_ctx.reset_request_id(token)
    return product_data_executor.submit(_runner)


analysis_job_store = AnalysisJobStore()
PRODUCT_DETAIL_FIELDS = ("brand", "product_name", "model_number", "color")


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


def _ensure_price_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload.setdefault("tax_excluded", None)
    payload.setdefault("tax_included", None)
    payload.setdefault("prices", [])
    return payload


def _sanitize_product_details(payload: Dict[str, Any]) -> Dict[str, Any]:
    description = payload.get("description")
    if not isinstance(description, dict):
        return payload
    details = description.get("product_details")
    if not isinstance(details, dict):
        return payload

    sanitized = dict(payload)
    sanitized_description = dict(description)
    sanitized_description["product_details"] = {
        field: details.get(field) if details.get(field) is not None else ""
        for field in PRODUCT_DETAIL_FIELDS
    }
    sanitized["description"] = sanitized_description
    return sanitized


def _has_direct_price(payload: Dict[str, Any]) -> bool:
    return payload.get("tax_excluded") is not None or payload.get("tax_included") is not None


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


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def prune_loop():
        while True:
            try:
                obs_prune(_obs_store, BASE_DIR / "logs" / "store",
                          settings.log_retention_days, settings.log_max_total_bytes)
            except Exception:
                pass
            await asyncio.sleep(settings.log_prune_interval_minutes * 60)

    task = asyncio.create_task(prune_loop())
    app.state.prune_task = task
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan, title="Mercari Image Analyzer", version="1.0.0")
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

app.include_router(build_obs_router(
    store=_obs_store,
    store_root=BASE_DIR / "logs" / "store",
    auth_dep=require_logs_auth(settings.logs_password),
))


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
    if _has_direct_price(classification) and not _has_direct_price(product_data):
        payload["tax_excluded"] = classification.get("tax_excluded")
        payload["tax_included"] = classification.get("tax_included")
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
    payload = _sanitize_product_details(payload)
    return _ensure_price_fields(payload)


def _pending_payload(job_id: str, classification: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(classification)
    payload["job_id"] = job_id
    payload["status"] = "product_pending"
    return _ensure_price_fields(payload)


def _safe_product_data_ms(future) -> Optional[float]:
    """Return the product_data_ms a finished future reported, or None.

    Safe to call on pending or errored futures: any failure path returns None
    rather than raising, since this helper is only used to surface optional
    timing fields back to the client.
    """
    if future is None or not future.done():
        return None
    try:
        result = future.result()
    except BaseException:
        return None
    if not isinstance(result, dict):
        return None
    timings = result.get("timings")
    if not isinstance(timings, dict):
        return None
    value = timings.get("product_data_ms")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _primary_elapsed_seconds(
    primary,
    primary_ok: bool,
    started_at: Optional[float],
) -> Optional[float]:
    """Compute primary's wall-clock elapsed time (in seconds) for threshold checks.

    When primary has completed successfully we trust the product_data_ms it
    reported (which was measured against the same submit timestamp). Otherwise
    we fall back to ``now - started_at`` so a still-running primary that has
    blown past the threshold is treated as exceeded.
    """
    if primary_ok:
        ms = _safe_product_data_ms(primary)
        if ms is not None:
            return ms / 1000.0
    if not primary.done() and started_at is not None:
        return time.monotonic() - float(started_at)
    return None


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

    primary_elapsed = _primary_elapsed_seconds(primary, primary_ok, started_at)
    # Use >= so a threshold of 0 still triggers when primary has not yet
    # produced a result, matching the original behaviour and side-stepping
    # the time.monotonic() granularity floor on Windows where two adjacent
    # reads inside the same tick can return the same float.
    primary_exceeded = (
        primary_elapsed is not None
        and fallback_timeout is not None
        and primary_elapsed >= float(fallback_timeout)
    )

    # Rule 1: prefer primary when it returned within the threshold.
    if primary_ok and not primary_exceeded:
        return "primary", primary.result(), None

    # Rule 2: primary blew past the threshold (still running or done late) →
    # use fallback as soon as it has data, regardless of primary's state.
    if primary_exceeded and fallback_ok:
        return "fallback", fallback.result(), None

    # Rule 3a: primary errored but fallback succeeded → use whatever we have.
    if primary_done and primary_error is not None and fallback_ok:
        return "fallback", fallback.result(), None

    # Rule 3 tail: primary is done (slow but successful) and fallback is
    # missing or also failed → keep the primary data instead of erroring.
    if primary_ok:
        return "primary", primary.result(), None

    # Both failed → propagate the primary error so the caller can decide.
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
    # Surface per-source timings so the UI can show both primary and fallback
    # wall times, even when only one of them was actually selected as source.
    primary_ms = _safe_product_data_ms(future)
    fallback_ms = _safe_product_data_ms(fallback_future)
    if primary_ms is not None or fallback_ms is not None:
        timings = dict(payload.get("timings") or {})
        if primary_ms is not None:
            timings["product_data_primary_ms"] = round(primary_ms, 2)
        if fallback_ms is not None:
            timings["product_data_fallback_ms"] = round(fallback_ms, 2)
        payload["timings"] = timings
    return payload


async def _prepare_image_payloads(
    image_list: List[UploadFile],
) -> Tuple[List[Tuple[bytes, str]], List[Dict[str, Any]]]:
    if not image_list:
        raise HTTPException(status_code=400, detail="Image files are required.")

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
    return image_payloads, image_processing


def _parse_original_product_data(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="original_product_data must be valid JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail="original_product_data must be a JSON object.",
        )
    return parsed


@app.get("/config", response_class=HTMLResponse,
         dependencies=[Depends(require_logs_auth(settings.logs_password))])
def config_page() -> HTMLResponse:
    try:
        return HTMLResponse(CONFIG_PAGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Config page not found.") from exc


@app.get("/logs", response_class=HTMLResponse,
         dependencies=[Depends(require_logs_auth(settings.logs_password))])
def logs_page():
    return FileResponse(BASE_DIR / "web" / "logs.html")


@app.get("/api/v1/config")
def read_config() -> Dict[str, Any]:
    return get_public_config(settings)


@app.put("/api/v1/config",
         dependencies=[Depends(require_logs_auth(settings.logs_password))])
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


_JOB_ID_PATH_PREFIX = "/api/v1/mercari/image/analyze/"


def _job_id_from_path(path: str) -> str:
    if path.startswith(_JOB_ID_PATH_PREFIX):
        return path[len(_JOB_ID_PATH_PREFIX):].split("/", 1)[0]
    return ""


def _job_id_from_response(body: bytes) -> str:
    try:
        obj = json.loads(body or b"{}")
    except Exception:
        return ""
    if isinstance(obj, dict):
        for key in ("job_id", "id"):
            v = obj.get(key)
            if isinstance(v, str):
                return v
    return ""


@app.middleware("http")
async def observe_request(request: Request, call_next):
    if (
        not settings.log_requests
        or request.url.path == "/health"
        or request.url.path.startswith("/api/v1/logs/")
        or request.url.path == "/logs"
    ):
        return await call_next(request)

    request_id = uuid.uuid4().hex
    token = obs_ctx.set_request_id(request_id)
    start = time.monotonic()
    body = b""
    status_code = 500
    error_message = ""
    response_body_bytes = b""

    try:
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()

            async def receive() -> dict:
                return {"type": "http.request", "body": body, "more_body": False}

            request = Request(request.scope, receive)

        content_type = request.headers.get("content-type", "")
        uploaded_images: List[Dict[str, Any]] = []
        if "multipart/form-data" in content_type and body:
            # parse to capture image bytes for archive
            async def _recv():
                return {"type": "http.request", "body": body, "more_body": False}

            tmp_req = _SReq(request.scope, _recv)
            try:
                form = await tmp_req.form()
                for key, value in form.multi_items():
                    if isinstance(value, _Upload):
                        data = await value.read()
                        suffix = "." + (value.content_type.rsplit("/", 1)[-1] if value.content_type else "bin")
                        uploaded_images.append({
                            "filename": value.filename or "",
                            "content_type": value.content_type or "",
                            "suffix": suffix,
                            "bytes": data,
                        })
            except Exception:
                pass

        try:
            recorder.start_request(
                request_id=request_id,
                method=request.method,
                endpoint=request.url.path,
                client_ip=(request.client.host if request.client else ""),
                user_agent=request.headers.get("user-agent", ""),
                language=request.query_params.get("language", "") or "",
                headers={
                    "content-type": content_type,
                    "user-agent": request.headers.get("user-agent", ""),
                    "content-length": request.headers.get("content-length", ""),
                },
                body_bytes=body,
                content_type=content_type,
                uploaded_images=uploaded_images,
            )
        except Exception:
            pass

        response = await call_next(request)
        status_code = response.status_code

        # buffer full response body for re-emission, keep capped slice for logging
        full_chunks: List[bytes] = []
        async for chunk in response.body_iterator:
            full_chunks.append(chunk)
        full_body_bytes = b"".join(full_chunks)
        cap = settings.log_response_max_bytes
        if cap > 0 and len(full_body_bytes) > cap:
            response_body_bytes = full_body_bytes[:cap]
        else:
            response_body_bytes = full_body_bytes

        new_response = _Resp(
            content=full_body_bytes,
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
            media_type=response.media_type,
        )
        new_response.headers["X-Request-Id"] = request_id
        return new_response

    except Exception as exc:
        error_message = repr(exc)
        raise
    finally:
        duration_ms = (time.monotonic() - start) * 1000.0
        try:
            job_id = _job_id_from_path(request.url.path) or _job_id_from_response(response_body_bytes)
            recorder.finalize_request(
                request_id=request_id,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error_message,
                response_body=response_body_bytes,
                job_id=job_id,
            )
        except Exception:
            pass
        obs_ctx.reset_request_id(token)


@app.post("/api/v1/mercari/image/analyze")
async def analyze_image(
    image_list: List[UploadFile] = File(...),
    language: str = Form(DEFAULT_LANGUAGE),
    debug: str = Form("false"),
    vision_model: str = Form(None),
    category_model: str = Form(None),
):
    image_payloads, image_processing = await _prepare_image_payloads(image_list)

    language = language or DEFAULT_LANGUAGE
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Invalid language.")

    debug_enabled = settings.enable_debug_param and parse_bool_param(debug, False)

    try:
        job_id = uuid.uuid4().hex
        # Capture the monotonic timestamp the moment we hand off the task to the
        # executor. We use this as both (a) the timing baseline reported back as
        # product_data_ms and (b) the threshold baseline for the fallback
        # decision logic. Aligning both on submit time means the timeout the
        # user configures actually maps to the elapsed time they observe.
        primary_submitted_at = time.monotonic()
        product_future = _submit_with_request_id(
            analyzer.generate_product_data,
            images=image_payloads,
            language=language,
            debug=debug_enabled,
            use_fallback_prompt=False,
            started_at=primary_submitted_at,
        )
        fallback_model = (settings.product_data_fallback_model or "").strip()
        fallback_future = None
        if fallback_model:
            fallback_submitted_at = time.monotonic()
            fallback_future = _submit_with_request_id(
                analyzer.generate_product_data,
                images=image_payloads,
                language=language,
                debug=debug_enabled,
                model_override=fallback_model,
                use_fallback_prompt=True,
                started_at=fallback_submitted_at,
            )
        fallback_timeout = float(settings.product_data_fallback_timeout_seconds)
        classification = await run_in_threadpool(
            analyzer.classify_first_image_categories,
            images=image_payloads,
            language=language,
            debug=debug_enabled,
            vision_model_override=vision_model,
            category_model_override=category_model,
            image_processing=image_processing,
        )
        analysis_job_store.put(
            job_id,
            classification=classification,
            future=product_future,
            fallback_future=fallback_future,
            started_at=primary_submitted_at,
            fallback_timeout=fallback_timeout,
        )
        result = _job_payload(
            job_id,
            classification,
            product_future,
            raise_product_errors=False,
            fallback_future=fallback_future,
            started_at=primary_submitted_at,
            fallback_timeout=fallback_timeout,
        )
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMAllAttemptsFailedError as exc:
        raise HTTPException(status_code=502, detail=_format_attempts_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc

    return JSONResponse(result)


@app.post("/api/v1/mercari/product-data/regenerate")
async def regenerate_product_data(
    image_list: List[UploadFile] = File(...),
    language: str = Form(DEFAULT_LANGUAGE),
    original_product_data: Optional[str] = Form(default=None),
    user_notes: Optional[str] = Form(default=None),
    debug: str = Form("false"),
):
    image_payloads, _image_processing = await _prepare_image_payloads(image_list)

    language = language or DEFAULT_LANGUAGE
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Invalid language.")

    original_payload = _parse_original_product_data(original_product_data)
    debug_enabled = settings.enable_debug_param and parse_bool_param(debug, False)

    try:
        result = await run_in_threadpool(
            analyzer.regenerate_product_data,
            images=image_payloads,
            language=language,
            original_product_data=original_payload,
            user_notes=user_notes or "",
            debug=debug_enabled,
        )
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMAllAttemptsFailedError as exc:
        raise HTTPException(status_code=502, detail=_format_attempts_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc

    result = _sanitize_product_details(result)
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


