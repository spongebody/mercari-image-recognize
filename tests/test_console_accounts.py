import json

import pytest

from app.console_accounts import (
    ALL_MENUS,
    ASSIGNABLE_MENUS,
    ConsoleAccountStore,
    hash_password,
    sanitize_subaccount_menus,
    verify_password,
)


def test_hash_password_roundtrips_and_rejects_wrong_password():
    encoded = hash_password("secret123")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("secret123", encoded) is True
    assert verify_password("nope", encoded) is False


def test_sanitize_subaccount_menus_filters_accounts_and_sorts():
    assert sanitize_subaccount_menus(["accounts", "evaluations", "test", "evaluations"]) == [
        "evaluations",
        "test",
    ]
    assert "accounts" in ALL_MENUS
    assert "accounts" not in ASSIGNABLE_MENUS


def test_store_initializes_missing_file_and_creates_user(tmp_path):
    path = tmp_path / "console_users.json"
    store = ConsoleAccountStore(path)

    created = store.create_user("model-tester", "secret123", ["evaluations"])

    assert created["username"] == "model-tester"
    assert created["menus"] == ["evaluations"]
    assert created["enabled"] is True
    assert "password_hash" not in created
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["users"][0]["password_hash"].startswith("pbkdf2_sha256$")


def test_store_authenticates_and_filters_disabled_users(tmp_path):
    store = ConsoleAccountStore(tmp_path / "console_users.json")
    store.create_user("model-tester", "secret123", ["evaluations"])
    assert store.authenticate("model-tester", "secret123").username == "model-tester"
    store.update_user("model-tester", enabled=False)
    assert store.authenticate("model-tester", "secret123") is None


def test_store_rejects_duplicate_empty_password_and_empty_menus(tmp_path):
    store = ConsoleAccountStore(tmp_path / "console_users.json")
    store.create_user("model-tester", "secret123", ["evaluations"])
    with pytest.raises(ValueError, match="already exists"):
        store.create_user("model-tester", "secret123", ["test"])
    with pytest.raises(ValueError, match="Password"):
        store.create_user("short-pass", "123", ["test"])
    with pytest.raises(ValueError, match="at least one menu"):
        store.create_user("no-menu", "secret123", ["accounts"])


def test_store_blocks_superadmin_username(tmp_path):
    store = ConsoleAccountStore(tmp_path / "console_users.json", superadmin_username="admin")
    with pytest.raises(ValueError, match="reserved"):
        store.create_user("admin", "secret123", ["test"])
