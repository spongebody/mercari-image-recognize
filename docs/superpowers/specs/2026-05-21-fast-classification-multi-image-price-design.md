# Fast Classification Multi-Image Price Design

## Goal

Return visible direct price fields in the first `/api/v1/mercari/image/analyze` response so application clients can display price data before the background product-data job finishes.

The first response must inspect all uploaded images for clearly visible actual product prices, while keeping category classification focused and fast.

## Current Flow

`main.py::analyze_image` submits `MercariAnalyzer.generate_product_data` to the background executor, then synchronously runs `MercariAnalyzer.classify_first_image_categories`.

The current fast classification path only sends the first image to `fast_vision` and returns category fields. `main.py::_pending_payload` then ensures `tax_excluded`, `tax_included`, and `prices` exist with default values. Real price fields arrive later from `generate_product_data`.

## Proposed Flow

Extend the existing `fast_vision` call instead of adding a separate price model call.

`classify_first_image_categories` will convert all uploaded images to data URLs and pass them to `_call_fast_classification_llm`. The prompt will instruct the model to:

- Use the first image as the primary evidence for `title`, `simple_description`, and `top_level_category`.
- Inspect every uploaded image for clearly visible actual product price labels, tags, receipts, stickers, or packaging prices.
- Return `tax_excluded` and `tax_included`.
- Return `null` for both direct price fields when no actual price is clearly visible.
- Avoid inferred reference prices in the fast path.

The fast classification result will normalize direct prices with the existing `_normalize_price_fields` helper, then force `prices` to `[]`.

## Response Contract

Initial `product_pending` responses may now include direct price fields:

```json
{
  "status": "product_pending",
  "tax_excluded": null,
  "tax_included": 1078,
  "prices": [],
  "categories": []
}
```

`prices` remains the background product-data chain's inferred reference-price field. The fast path will not infer three reference prices.

## Merge Behavior

When the background product-data result is merged:

- If product data returns a direct price, it wins because that chain uses the fuller product-data prompt.
- If product data has no direct price but fast classification did, keep the fast direct price.
- If neither chain has a direct price, product data may still provide inferred `prices`.

This avoids completed polling responses losing a visible price that was already returned in the first response.

## Error Handling

No new external stage is introduced. Price extraction failures are handled as part of the existing `fast_vision` parsing and fallback behavior.

If a model omits the new price fields, normalization treats them as `null`, and the API still returns the existing default `null` / `null` / `[]` fields.

## Tests

Add or update focused tests to verify:

- Fast classification sends all uploaded images to `fast_vision`.
- Fast classification prompt asks for `tax_excluded` and `tax_included`.
- Initial `product_pending` responses include direct price fields from classification.
- Completed responses preserve fast direct prices when product data has no direct price.
- Completed responses use product-data direct prices when product data provides them.

## Documentation

Update `README.md` and `API.md` so the public contract says initial responses can include direct visible prices from the fast multi-image price extraction path, while inferred `prices` still comes from product data.
