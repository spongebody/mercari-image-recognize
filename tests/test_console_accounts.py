import hashlib
import json
import time

import pytest

from app.console_accounts import (
    ALL_MENUS,
    ASSIGNABLE_MENUS,
    ConsoleAccountStore,
    SUBACCOUNT_ROLE,
    SUPERADMIN_ROLE,
    hash_password,
    sanitize_subaccount_menus,
    verify_password,
)


def _encoded_password_hash(password, *, iterations=260_000, salt=b"0" * 16):
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def test_hash_password_roundtrips_and_rejects_wrong_password():
    encoded = hash_password("secret123")
    algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
    assert algorithm == "pbkdf2_sha256"
    assert iterations == "260000"
    assert len(bytes.fromhex(salt_hex)) == 16
    assert len(bytes.fromhex(digest_hex)) == hashlib.sha256().digest_size
    assert verify_password("secret123", encoded) is True
    assert verify_password("nope", encoded) is False


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$260000$00",
        "pbkdf2_sha256$not-int$00$00",
        "pbkdf2_sha256$260000$not-hex$00",
        "pbkdf2_sha256$260000$00$not-hex",
        "sha1$260000$00$00",
    ],
)
def test_verify_password_rejects_malformed_hashes(encoded):
    assert verify_password("secret123", encoded) is False


def test_verify_password_rejects_hashes_with_non_spec_iterations():
    encoded = _encoded_password_hash("secret123", iterations=1)

    assert verify_password("secret123", encoded) is False


def test_verify_password_rejects_hashes_with_wrong_salt_length():
    encoded = _encoded_password_hash("secret123", salt=b"0")

    assert verify_password("secret123", encoded) is False


def test_sanitize_subaccount_menus_filters_accounts_and_sorts():
    assert sanitize_subaccount_menus(["accounts", "evaluations", "test", "evaluations"]) == [
        "evaluations",
        "test",
    ]


def test_console_account_constants_match_required_values():
    assert ALL_MENUS == ("test", "config", "evaluations", "logs", "accounts")
    assert ASSIGNABLE_MENUS == ("test", "config", "evaluations", "logs")
    assert SUPERADMIN_ROLE == "superadmin"
    assert SUBACCOUNT_ROLE == "subaccount"


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


def test_store_list_users_returns_public_users_without_hashes(tmp_path):
    store = ConsoleAccountStore(tmp_path / "console_users.json")
    store.create_user("model-tester", "secret123", ["evaluations"])
    store.create_user("log-viewer", "secret456", ["logs", "accounts"])

    users = store.list_users()

    assert [user["username"] for user in users] == ["model-tester", "log-viewer"]
    assert users[0]["menus"] == ["evaluations"]
    assert users[1]["menus"] == ["logs"]
    assert all("password_hash" not in user for user in users)
    assert all(user["enabled"] is True for user in users)


def test_store_authenticates_and_filters_disabled_users(tmp_path):
    store = ConsoleAccountStore(tmp_path / "console_users.json")
    store.create_user("model-tester", "secret123", ["evaluations"])
    assert store.authenticate("model-tester", "secret123").username == "model-tester"
    store.update_user("model-tester", enabled=False)
    assert store.authenticate("model-tester", "secret123") is None


def test_store_get_user_returns_enabled_user_only(tmp_path):
    store = ConsoleAccountStore(tmp_path / "console_users.json")
    store.create_user("model-tester", "secret123", ["test", "evaluations"])

    user = store.get_user("model-tester")

    assert user.username == "model-tester"
    assert user.menus == ("evaluations", "test")
    assert user.enabled is True
    store.update_user("model-tester", enabled=False)
    assert store.get_user("model-tester") is None
    assert store.get_user("missing") is None


def test_store_update_user_changes_password_menus_enabled_and_updated_at(tmp_path):
    path = tmp_path / "console_users.json"
    store = ConsoleAccountStore(path)
    created = store.create_user("model-tester", "secret123", ["evaluations"])
    original_raw = json.loads(path.read_text(encoding="utf-8"))["users"][0]
    time.sleep(0.001)

    updated = store.update_user(
        "model-tester",
        password="secret456",
        menus=["accounts", "logs", "test", "logs"],
        enabled=False,
    )

    assert updated["menus"] == ["logs", "test"]
    assert updated["enabled"] is False
    assert updated["updated_at"] != created["updated_at"]
    assert store.authenticate("model-tester", "secret456") is None
    updated_raw = json.loads(path.read_text(encoding="utf-8"))["users"][0]
    assert updated_raw["password_hash"] != original_raw["password_hash"]
    store.update_user("model-tester", enabled=True)
    assert store.authenticate("model-tester", "secret123") is None
    assert store.authenticate("model-tester", "secret456").menus == ("logs", "test")


def test_store_delete_user_removes_user_and_rejects_missing_user(tmp_path):
    store = ConsoleAccountStore(tmp_path / "console_users.json")
    store.create_user("model-tester", "secret123", ["evaluations"])

    store.delete_user("model-tester")

    assert store.list_users() == []
    assert store.get_user("model-tester") is None
    assert store.authenticate("model-tester", "secret123") is None
    with pytest.raises(KeyError):
        store.delete_user("model-tester")


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
