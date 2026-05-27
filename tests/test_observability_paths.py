from pathlib import Path

import pytest

from app.observability.paths import (
    artifact_dir,
    resolve_artifact,
    request_dir_for_date,
)


def test_artifact_dir_uses_iso_date_then_request_id(tmp_path: Path):
    d = artifact_dir(tmp_path, "2026-05-26", "abc123")
    assert d == tmp_path / "2026-05-26" / "abc123"


def test_resolve_artifact_inside_dir_ok(tmp_path: Path):
    base = tmp_path / "2026-05-26" / "abc"
    base.mkdir(parents=True)
    (base / "request.json").write_text("{}")
    p = resolve_artifact(tmp_path, "2026-05-26", "abc", "request.json")
    assert p == base / "request.json"


def test_resolve_artifact_rejects_traversal(tmp_path: Path):
    base = tmp_path / "2026-05-26" / "abc"
    base.mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "abc", "../../etc/passwd")
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "abc", "/abs/path")


def test_request_dir_for_date_lists_subdirs(tmp_path: Path):
    (tmp_path / "2026-05-26" / "r1").mkdir(parents=True)
    (tmp_path / "2026-05-26" / "r2").mkdir(parents=True)
    dirs = sorted(p.name for p in request_dir_for_date(tmp_path, "2026-05-26"))
    assert dirs == ["r1", "r2"]


def test_resolve_artifact_rejects_bad_date_str(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-5-26", "abc", "f.json")     # wrong format
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "../../etc", "abc", "f.json")     # traversal


def test_resolve_artifact_rejects_bad_request_id(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "../evil", "f.json")
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "abc/def", "f.json")
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "abc def", "f.json")  # space disallowed


def test_resolve_artifact_rejects_control_chars(tmp_path: Path):
    base = tmp_path / "2026-05-26" / "abc"
    base.mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "abc", "f\x00.json")
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "abc", "f\n.json")
