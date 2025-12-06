from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.config import load_settings
from app.constants import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.data.brands import BrandStore
from app.data.categories import CategoryStore
from app.errors import BadRequestError, LLMRequestError
from app.llm.client import OpenRouterClient
from app.service import MercariAnalyzer
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
)
category_client = OpenRouterClient(
    api_key=settings.openrouter_api_key,
    base_url=settings.openrouter_base_url,
    timeout=settings.request_timeout,
    referer=settings.openrouter_referer,
    app_name=settings.openrouter_app_name,
)
price_client = OpenRouterClient(
    api_key=settings.openrouter_api_key,
    base_url=settings.openrouter_base_url,
    timeout=settings.request_timeout,
    referer=settings.openrouter_referer,
    app_name=settings.openrouter_app_name,
)

analyzer = MercariAnalyzer(
    settings=settings,
    brand_store=brand_store,
    category_store=category_store,
    vision_client=vision_client,
    category_client=category_client,
    price_client=price_client,
)

app = FastAPI(title="Mercari Image Analyzer", version="1.0.0")

# Allow local dev CORS for the test page or other origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/v1/mercari/image/analyze")
async def analyze_image(
    image: UploadFile = File(...),
    language: str = Form(DEFAULT_LANGUAGE),
    debug: str = Form("false"),
    category_count: int = Form(1),
    price_strategy: str = Form("dedicated"),
    vision_model: str = Form(None),
    category_model: str = Form(None),
    price_model: str = Form(None),
):
    if not image:
        raise HTTPException(status_code=400, detail="Image file is required.")

    if image.content_type not in settings.allowed_mime_types:
        raise HTTPException(status_code=400, detail="Unsupported image type.")

    try:
        data = await image.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file.")

    if not data:
        raise HTTPException(status_code=400, detail="Uploaded image is empty.")

    if len(data) > settings.max_image_bytes:
        raise HTTPException(status_code=400, detail="Image is too large.")

    language = language or DEFAULT_LANGUAGE
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Invalid language.")

    debug_enabled = settings.enable_debug_param and parse_bool_param(debug, False)
    category_count = max(1, min(category_count, 3))
    price_strategy = price_strategy or "dedicated"

    try:
        result = await run_in_threadpool(
            analyzer.analyze,
            image_bytes=data,
            mime_type=image.content_type or "application/octet-stream",
            language=language,
            debug=debug_enabled,
            category_limit=category_count,
            price_strategy=price_strategy,
            vision_model_override=vision_model,
            category_model_override=category_model,
            price_model_override=price_model,
        )
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc

    return JSONResponse(result)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "models": {
            "vision_model": settings.vision_model,
            "vision_model_online": settings.vision_model_online,
            "category_model": settings.category_model,
            "price_model": settings.price_model,
        },
    }
