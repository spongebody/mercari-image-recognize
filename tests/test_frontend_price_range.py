from pathlib import Path


HTML = Path(__file__).resolve().parents[1] / "web" / "index.html"


def test_price_test_ui_has_reference_price_range_display():
    html = HTML.read_text(encoding="utf-8")

    assert "fieldReferencePriceRange" in html
    assert "AI参考价格区间" in html
    assert "AI参考価格帯" in html
    assert "function formatReferencePriceRange" in html
    assert "price-range-value" in html
