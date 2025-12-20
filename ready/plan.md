
# Plan

  Add a title-based classification endpoint that reuses the existing top-level→category-path selection flow, with a fallback to image recognition when
  title classification fails, and update the frontend tester to support the new API.

  ## Requirements

  - New POST endpoint that accepts title (required), image_url (optional), language (default ja).
  - New prompts PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT and PRODUCT_TITLE_CATEGORY_USER_PROMPT that return only top_level_category.
  - Reuse existing category selection logic after top_level_category mapping.
  - If title-based classification fails, call image recognition with image_url and still return best_target_path.
  - Response JSON: best_target_path (required), alternatives (paths).

  ## Scope

  - In: prompt additions, service logic, new endpoint, fallback image handling, frontend test UI changes.
  - Out: changing existing image analyze output shape or pricing logic.

  ## Files and entry points

  - app/llm/prompts.py (new title prompts).
  - app/service.py (title classification + fallback flow).
  - main.py (new FastAPI route).
  - app/utils.py (optional helper for fetching/validating image_url).
  - web/index.html (test UI updates).

  ## Data model / API changes

  - Request: JSON body { title, image_url?, language? } (or Form if you prefer).
  - Response: { best_target_path, alternatives }.

  ## Action items

  [ ] Add PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT + PRODUCT_TITLE_CATEGORY_USER_PROMPT returning only top_level_category.
  [ ] Implement title-based classification: call LLM → map top_level_category → reuse _choose_categories to validate paths.
  [ ] Add fallback: if title classification fails or no valid categories, fetch image_url and run image pipeline to ensure best_target_path.
  [ ] Add new FastAPI endpoint with input validation and error handling for missing/invalid image_url on fallback.
  [ ] Update web/index.html to support title-based API testing and display best_target_path + alternatives.

  ## Testing and validation

  - Manual API tests: known titles → verify best_target_path.
  - Manual fallback tests: ambiguous title + valid image_url → ensure fallback produces best_target_path.
  - UI: toggle to new API and verify result rendering.

  ## Risks and edge cases

  - Title prompt returns invalid top-level category → fallback required(must return top-level category).
  - image_url missing or unreachable when fallback needed → must return clear error.
  - Alternatives format expectations:structured objects.

## Test example
- CB1000SF専用 ハイスロKIT・カラーアウターSET（グリーンアウター×ブルーボディー：純正キャブレター用
- CB400SS専用 ハイスロKIT・ブラックアウターSET（ブラックアウター×レッドボディー：社外品キャブレター用
- CB400SF VTEC REVO 専用 ハイスロKIT・ブラックアウターSET（ブラックアウター×シルバーボディー：社外品キャブレター用
