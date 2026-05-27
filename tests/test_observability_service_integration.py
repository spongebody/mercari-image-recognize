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
    analyzer._record_stage = svc.MercariAnalyzer._record_stage.__get__(analyzer)

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


def test_showcase_reuses_contextvar_request_id(monkeypatch, tmp_path):
    from app.observability import context as obs_ctx
    from app.showcase import service as sc_module

    client = MagicMock()
    client.generate_image.return_value = type("R", (), {
        "image": type("Img", (), {"mime_type": "image/png", "base64_data": ""})(),
        "upstream_status_code": 200,
        "response_body": {},
        "attempts": 1,
        "attempt_records": [],
        "model": "m",
    })()
    storage = MagicMock()
    storage.save_input_image.return_value = tmp_path / "in.png"
    storage.save_output_image.return_value = tmp_path / "out.png"
    archive = MagicMock()
    service = sc_module.ShowcaseService(
        model="m", storage_manager=storage, archive_writer=archive, client=client
    )

    token = obs_ctx.set_request_id("rid-showcase")
    try:
        resp = service.generate_showcase(
            upload_filename="a.png", content_type="image/png",
            image_bytes=b"x", prompt_hint=None,
        )
    finally:
        obs_ctx.reset_request_id(token)

    assert resp["request_id"] == "rid-showcase"
