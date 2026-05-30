"""Time the price-only link end-to-end on a set of local images.

Usage:
    uv run python scripts/test_price_only.py [image ...] [--runs N] [--model M]

Defaults to the two images under data/images and 3 timed runs. Only the
extract_prices() call is timed (image read + compression happen first, mirroring
the real request path but excluded from the LLM timing).
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.data.brands import BrandStore
from app.data.categories import CategoryStore
from app.image_processing import compress_image_if_needed
from app.llm.client import OpenRouterClient
from app.service import MercariAnalyzer

DEFAULT_IMAGES = [
    ROOT / "data" / "images" / "price_1.jpg",
    ROOT / "data" / "images" / "price_2.jpg",
]
MIME_BY_SUFFIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _load_payloads(paths, threshold_bytes):
    payloads = []
    for p in paths:
        raw = Path(p).read_bytes()
        mime = MIME_BY_SUFFIX.get(Path(p).suffix.lower(), "image/jpeg")
        processed = compress_image_if_needed(
            image_bytes=raw, mime_type=mime, threshold_bytes=threshold_bytes
        )
        payloads.append((processed.data, processed.mime_type))
        print(
            f"  {Path(p).name}: {processed.original_bytes} -> {processed.processed_bytes} bytes "
            f"(compressed={processed.compressed}, mime={processed.mime_type})"
        )
    return payloads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="*", help="image paths (default: data/images/price_1.jpg price_2.jpg)")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default=None, help="override vision model")
    args = parser.parse_args()

    images = args.images or [str(p) for p in DEFAULT_IMAGES]
    settings = load_settings()
    print(f"Vision model: {args.model or settings.vision_model}")
    print(f"Vision fallbacks: {settings.vision_fallback_models}")
    print(f"Reasoning payload: {settings.reasoning}")
    print(f"Images ({len(images)}):")

    payloads = _load_payloads(images, settings.image_compression_threshold_bytes)

    vision_client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        timeout=settings.request_timeout,
        referer=settings.openrouter_referer,
        app_name=settings.openrouter_app_name,
        reasoning=settings.reasoning,
    )
    analyzer = MercariAnalyzer(
        settings=settings,
        brand_store=BrandStore(settings.brand_csv_path),
        category_store=CategoryStore(settings.category_csv_path),
        vision_client=vision_client,
        category_client=vision_client,
    )

    durations = []
    for i in range(1, args.runs + 1):
        t0 = time.monotonic()
        result = analyzer.extract_prices(payloads, debug=True, model_override=args.model)
        elapsed = time.monotonic() - t0
        durations.append(elapsed)
        debug = result.pop("_debug", {})
        attempts = debug.get("attempts", {}).get("price_only", [])
        used = next((a for a in attempts if a.get("error_kind") == "ok"), None)
        print(
            f"\nRun {i}: {elapsed:.2f}s  ->  "
            f"tax_excluded={result.get('tax_excluded')} "
            f"tax_included={result.get('tax_included')} prices={result.get('prices')}"
        )
        print(f"  attempts: {[(a['model'], a['error_kind'], round(a['latency_ms'])) for a in attempts]}")
        if used:
            print(f"  served by: {used['model']} (LLM latency {round(used['latency_ms'])}ms)")
        print(f"  raw: {debug.get('price_ai_raw')}")

    print("\n=== Summary ===")
    print(f"runs={len(durations)}  min={min(durations):.2f}s  "
          f"max={max(durations):.2f}s  avg={sum(durations)/len(durations):.2f}s")


if __name__ == "__main__":
    main()
