from __future__ import annotations

from datetime import datetime, timezone

from app.evaluation.image_model_evaluation import (
    RESULT_FIELDS,
    ModelCombination,
    build_model_combinations,
    build_result_row,
    summarize_rows,
)
from scripts.run_image_model_tests import format_output_stamp
from scripts.run_image_model_tests import format_progress_message
from scripts.run_image_model_tests import output_stamp_from_utc


def test_progress_message_includes_elapsed_and_eta():
    message = format_progress_message(
        task_index=3,
        total_tasks=10,
        status="ok",
        model="vision-a",
        reasoning_effort="none",
        elapsed_s=30.0,
    )

    assert message == "[3/10] ok vision-a / none | elapsed 30s | eta 1m10s"


def test_output_stamp_uses_yyyy_mm_dd_hh_mm_with_dashes():
    assert format_output_stamp(datetime(2026, 6, 2, 15, 28, 59)) == "2026-06-02-15-28"


def test_output_stamp_uses_beijing_time_for_utc_input():
    assert (
        output_stamp_from_utc(datetime(2026, 6, 2, 7, 28, 59, tzinfo=timezone.utc))
        == "2026-06-02-15-28"
    )


def test_result_fields_are_reviewer_friendly_and_include_model_dimensions():
    assert RESULT_FIELDS == [
        "itemName",
        "genreId",
        "image",
        "brand",
        "visionModel",
        "categoryModel",
        "productDataModel",
        "reasoningEffort",
        "aiCategory",
        "aiCategoryPath",
        "aiCategoryConfidence",
        "aiBrand",
        "aiTitle",
        "categoryDurationS",
        "productDataDurationS",
        "totalDurationS",
        "customerCategoryCheck",
        "customerBrandCheck",
        "customerNotes",
    ]


def test_result_fields_include_customer_notes():
    assert "customerNotes" in RESULT_FIELDS
    assert RESULT_FIELDS[-1] == "customerNotes"


def test_build_result_row_extracts_predictions_and_keeps_customer_checks_blank():
    combo = ModelCombination(
        vision_model="openai/gpt-4o-mini",
        category_model="openai/gpt-4o-mini",
        product_data_model="google/gemini-2.5-flash",
        reasoning_effort="low",
    )
    case = {
        "itemName": "source title",
        "genreId": "112747",
        "image": "https://example.test/item-01.jpg|https://example.test/item-02.jpg",
        "brand": "CASIO",
    }
    classification = {
        "best_category_id": "112747",
        "best_target_path": "腕時計",
        "categories": [
            {
                "id": "112747",
                "name": "腕時計",
                "confidence": 0.87654,
            }
        ],
        "timings": {"classification_ms": 1234.5},
    }
    product_data = {
        "title": "CASIO G-SHOCK ブラック デジアナ 腕時計",
        "brand_name": "Casio",
        "timings": {"product_data_ms": 4567.8},
    }

    row = build_result_row(
        case,
        combo,
        classification,
        product_data,
        total_duration_s=5.812,
    )

    assert row == {
        "itemName": "source title",
        "genreId": "112747",
        "image": "https://example.test/item-01.jpg|https://example.test/item-02.jpg",
        "brand": "CASIO",
        "visionModel": "openai/gpt-4o-mini",
        "categoryModel": "openai/gpt-4o-mini",
        "productDataModel": "google/gemini-2.5-flash",
        "reasoningEffort": "low",
        "aiCategory": "112747",
        "aiCategoryPath": "腕時計",
        "aiCategoryConfidence": "0.877",
        "aiBrand": "Casio",
        "aiTitle": "CASIO G-SHOCK ブラック デジアナ 腕時計",
        "categoryDurationS": "1.234",
        "productDataDurationS": "4.568",
        "totalDurationS": "5.812",
        "customerCategoryCheck": "",
        "customerBrandCheck": "",
        "customerNotes": "",
    }


def test_summarize_rows_includes_customer_reviewed_accuracy():
    rows = [
        {
            "genreId": "100040",
            "aiCategory": "100040",
            "brand": "ASUS",
            "aiBrand": "ASUS",
            "visionModel": "vision-a",
            "categoryModel": "category-a",
            "productDataModel": "product-a",
            "reasoningEffort": "none",
            "customerCategoryCheck": "",
            "customerBrandCheck": "",
        },
        {
            "genreId": "100181",
            "aiCategory": "565105",
            "brand": "recolte",
            "aiBrand": "",
            "visionModel": "vision-a",
            "categoryModel": "category-a",
            "productDataModel": "product-a",
            "reasoningEffort": "none",
            "customerCategoryCheck": "ACCEPTABLE",
            "customerBrandCheck": "NG",
        },
    ]

    summary = summarize_rows(rows)

    assert summary["overall"]["categoryReviewedCorrect"] == 2
    assert summary["overall"]["brandReviewedCorrect"] == 1
    assert summary["overall"]["categoryReviewedAccuracy"] == 1.0
    assert summary["overall"]["brandReviewedAccuracy"] == 0.5
    assert summary["overall"]["categoryPendingReview"] == 0
    assert summary["overall"]["brandPendingReview"] == 0


def test_summarize_rows_groups_by_model_dimensions_and_uses_normalized_brand_match():
    rows = [
        {
            "genreId": "112747",
            "brand": "LOUIS VUITTON",
            "visionModel": "vision-a",
            "categoryModel": "category-a",
            "productDataModel": "product-a",
            "reasoningEffort": "none",
            "aiCategory": "112747",
            "aiBrand": "louis vuitton",
        },
        {
            "genreId": "110942",
            "brand": "CASIO",
            "visionModel": "vision-a",
            "categoryModel": "category-a",
            "productDataModel": "product-a",
            "reasoningEffort": "none",
            "aiCategory": "112747",
            "aiBrand": "Seiko",
        },
    ]

    summary = summarize_rows(rows)

    assert summary["overall"]["total"] == 2
    assert summary["overall"]["categoryCorrect"] == 1
    assert summary["overall"]["brandCorrect"] == 1
    assert summary["overall"]["categoryAccuracy"] == 0.5
    assert summary["overall"]["brandAccuracy"] == 0.5
    assert summary["byModel"][0]["visionModel"] == "vision-a"
    assert summary["byModel"][0]["reasoningEffort"] == "none"
    assert summary["byModel"][0]["categoryAccuracy"] == 0.5


def test_build_model_combinations_expands_cartesian_product():
    combos = build_model_combinations(
        vision_models=["vision-a", "vision-b"],
        category_models=["category-a"],
        product_data_models=["product-a"],
        reasoning_efforts=["none", "medium"],
    )

    assert combos == [
        ModelCombination("vision-a", "category-a", "product-a", "none"),
        ModelCombination("vision-a", "category-a", "product-a", "medium"),
        ModelCombination("vision-b", "category-a", "product-a", "none"),
        ModelCombination("vision-b", "category-a", "product-a", "medium"),
    ]


def test_summary_includes_average_durations():
    rows = [
        {
            "genreId": "1", "aiCategory": "1", "brand": "nike", "aiBrand": "nike",
            "categoryDurationS": "1.0", "productDataDurationS": "2.0", "totalDurationS": "3.0",
        },
        {
            "genreId": "2", "aiCategory": "2", "brand": "nike", "aiBrand": "nike",
            # productDataDurationS 为空串（行失败时的留空格式），不应计入均值
            "categoryDurationS": "2.0", "productDataDurationS": "", "totalDurationS": "5.0",
        },
    ]

    summary = summarize_rows(rows)

    assert summary["overall"]["avgTotalDurationS"] == 4.0
    assert summary["overall"]["avgCategoryDurationS"] == 1.5
    assert summary["overall"]["avgProductDataDurationS"] == 2.0
    assert summary["byModel"][0]["avgTotalDurationS"] == 4.0


def test_summary_average_durations_none_when_all_missing():
    rows = [{"genreId": "1", "aiCategory": "1", "brand": "", "aiBrand": ""}]

    summary = summarize_rows(rows)

    assert summary["overall"]["avgTotalDurationS"] is None
    assert summary["overall"]["avgCategoryDurationS"] is None
    assert summary["overall"]["avgProductDataDurationS"] is None
