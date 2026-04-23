# Mercari Brand Source Design

**Background**

The current brand lookup flow initializes `BrandStore` from `Settings.brand_csv_path` in `main.py`, loads rows in `app/data/brands.py`, and returns `brand_name` plus `brand_id_obj` from `app/service.py` without transforming the external response shape.

The existing default brand source is `data/rdx_brand.csv`. The requested change is to switch the source to `data/mercari_brand.csv` while keeping the final response fields unchanged.

**Requirements**

1. Change the default brand CSV source from `data/rdx_brand.csv` to `data/mercari_brand.csv`.
2. Normalize `data/mercari_brand.csv` into a UTF-8 readable standard file with English headers.
3. Enrich the normalized Mercari brand file with platform brand ids derived from `data/rdx_brand.csv` by matching `rdx_brand.meru_id == mercari_brand.id`.
4. Ensure the normalized file contains these columns:
   `id,name,name_jp,name_en,rakuten_id,yshop_id,yauc_id,meru_id,ebay_id,rakuma_id,amazon_id,qoo10_id`
5. Ensure `meru_id` is always populated and equals `id`.
6. Use empty strings for missing platform ids.
7. Keep API response fields unchanged.

**Design**

Add an offline normalization script that reads `data/mercari_brand.csv`, accepts either the current raw CP932/Japanese-header format or the normalized UTF-8 format, and writes back the normalized UTF-8 CSV.

The script will:

- detect a supported input format for `data/mercari_brand.csv`
- map source columns to `id`, `name`, `name_jp`, and `name_en`
- build a lookup from `data/rdx_brand.csv` keyed by non-empty `meru_id`
- write platform ids from the matched RDX row into the normalized Mercari row
- force `meru_id = id`
- convert `0` and missing values to empty strings for non-Mercari platform ids

Runtime code remains simple:

- `app/config.py` points the default `BRAND_CSV_PATH` at `data/mercari_brand.csv`
- `BrandStore` continues to consume an English-header UTF-8 CSV
- `app/service.py` continues returning `brand_name` and `brand_id_obj` without schema changes

**Verification Strategy**

1. Add unit tests for normalized brand loading and id cleaning behavior.
2. Add unit tests for the normalization script using small fixture CSV inputs.
3. Run the normalization script against the real dataset.
4. Run the full test suite after code and data updates.
