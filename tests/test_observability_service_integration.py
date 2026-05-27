from unittest.mock import MagicMock

from app.observability import context as obs_ctx


def test_choose_categories_records_llm_stage(monkeypatch, tmp_path):
    """Service layer routes LLM logging through the recorder."""
    from app import service as svc

    recorder = MagicMock()
    monkeypatch.setattr(svc, "recorder", recorder, raising=False)

    analyzer = MagicMock()
    parsed = {"category_paths": [{"name": "x"}]}
    raw = {"usage": {"total_tokens": 50}, "cost": 0.0001}
    attempts = [type("A", (), {"__dict__": {"model": "m", "attempt": 1, "error_kind": "ok",
                                            "message": "", "latency_ms": 10.0, "status_code": 200}})()]

    analyzer.category_caller = MagicMock()
    analyzer.category_caller.call_and_parse.return_value = (parsed, raw, attempts)
    analyzer.settings = MagicMock(category_model="m", category_fallback_models=[])
    analyzer.category_store = MagicMock()
    analyzer.category_store.get_categories_by_group.return_value = [{"name": "x"}]

    token = obs_ctx.set_request_id("rid-service")
    try:
        svc.MercariAnalyzer._choose_categories(
            analyzer, title="t", description="d", brand_for_prompt="b", group_name="g"
        )
    finally:
        obs_ctx.reset_request_id(token)

    recorder.record_llm_stage.assert_called_once()
    kwargs = recorder.record_llm_stage.call_args.kwargs
    assert kwargs["stage"] == "category"
    assert kwargs["request_id"] == "rid-service"
