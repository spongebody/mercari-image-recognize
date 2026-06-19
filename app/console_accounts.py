from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any, Iterable


ALL_MENUS = ("test", "config", "evaluations", "logs", "accounts")
ASSIGNABLE_MENUS = ("test", "config", "evaluations", "logs")

SUPERADMIN_ROLE = "superadmin"
SUBACCOUNT_ROLE = "subaccount"

_HASH_ALGORITHM = "pbkdf2_sha256"
_HASH_ITERATIONS = 260_000
_SALT_BYTES = 16
_STORE_VERSION = 1


@dataclass(frozen=True)
class ConsoleUser:
    username: str
    menus: tuple[str, ...]
    enabled: bool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _HASH_ITERATIONS,
    )
    return f"{_HASH_ALGORITHM}${_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != _HASH_ALGORITHM:
            return False
        if int(iterations) != _HASH_ITERATIONS:
            return False
        salt = bytes.fromhex(salt_hex)
        if len(salt) != _SALT_BYTES:
            return False
        expected = bytes.fromhex(digest_hex)
        if len(expected) != hashlib.sha256().digest_size:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            _HASH_ITERATIONS,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def sanitize_subaccount_menus(menus: Iterable[str]) -> list[str]:
    allowed = set(ASSIGNABLE_MENUS)
    return sorted({menu for menu in menus if menu in allowed})


class ConsoleAccountStore:
    def __init__(self, path: str | os.PathLike[str], superadmin_username: str = "admin"):
        self.path = Path(path)
        self.superadmin_username = superadmin_username.strip()
        self._ensure_file()

    def list_users(self) -> list[dict[str, Any]]:
        data = self._read_data()
        return [self._public_user(user) for user in data["users"]]

    def create_user(
        self,
        username: str,
        password: str,
        menus: Iterable[str],
        *,
        enabled: bool = True,
    ) -> dict[str, Any]:
        normalized_username = self._validate_username(username)
        if self._same_username(normalized_username, self.superadmin_username):
            raise ValueError("Username is reserved for the superadmin account.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")

        sanitized_menus = sanitize_subaccount_menus(menus)
        if not sanitized_menus:
            raise ValueError("Subaccounts must have at least one menu.")

        data = self._read_data()
        if self._find_user(data, normalized_username) is not None:
            raise ValueError(f"User {normalized_username!r} already exists.")

        timestamp = _now_iso()
        user = {
            "username": normalized_username,
            "role": SUBACCOUNT_ROLE,
            "password_hash": hash_password(password),
            "menus": sanitized_menus,
            "enabled": bool(enabled),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        data["users"].append(user)
        self._write_data(data)
        return self._public_user(user)

    def update_user(
        self,
        username: str,
        *,
        password: str | None = None,
        menus: Iterable[str] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        normalized_username = username.strip()
        data = self._read_data()
        user = self._find_user(data, normalized_username)
        if user is None:
            raise KeyError(normalized_username)

        if password is not None:
            if len(password) < 6:
                raise ValueError("Password must be at least 6 characters.")
            user["password_hash"] = hash_password(password)
        if menus is not None:
            sanitized_menus = sanitize_subaccount_menus(menus)
            if not sanitized_menus:
                raise ValueError("Subaccounts must have at least one menu.")
            user["menus"] = sanitized_menus
        if enabled is not None:
            user["enabled"] = bool(enabled)

        user["updated_at"] = _now_iso()
        self._write_data(data)
        return self._public_user(user)

    def delete_user(self, username: str) -> None:
        normalized_username = username.strip()
        data = self._read_data()
        next_users = [
            user
            for user in data["users"]
            if not self._same_username(str(user.get("username", "")), normalized_username)
        ]
        if len(next_users) == len(data["users"]):
            raise KeyError(normalized_username)
        data["users"] = next_users
        self._write_data(data)

    def get_user(self, username: str) -> ConsoleUser | None:
        user = self._find_user(self._read_data(), username.strip())
        if user is None or not user.get("enabled", True):
            return None
        menus = tuple(sanitize_subaccount_menus(user.get("menus", [])))
        if not menus:
            return None
        return ConsoleUser(
            username=str(user["username"]),
            menus=menus,
            enabled=bool(user.get("enabled", True)),
        )

    def authenticate(self, username: str, password: str) -> ConsoleUser | None:
        data = self._read_data()
        user = self._find_user(data, username.strip())
        if user is None or not user.get("enabled", True):
            return None
        if not sanitize_subaccount_menus(user.get("menus", [])):
            return None
        if not verify_password(password, str(user.get("password_hash", ""))):
            return None
        return ConsoleUser(
            username=str(user["username"]),
            menus=tuple(sanitize_subaccount_menus(user.get("menus", []))),
            enabled=bool(user.get("enabled", True)),
        )

    def _ensure_file(self) -> None:
        if self.path.exists():
            return
        self._write_data({"version": _STORE_VERSION, "users": []})

    def _read_data(self) -> dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        if not isinstance(data, dict):
            raise ValueError("Console account store must contain a JSON object.")
        if data.get("version") != _STORE_VERSION:
            raise ValueError("Unsupported console account store version.")
        if not isinstance(data.get("users"), list):
            raise ValueError("Console account store users must be a list.")
        return data

    def _write_data(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_name = temp_file.name
                json.dump(data, temp_file, indent=2, sort_keys=True)
                temp_file.write("\n")
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def _find_user(self, data: dict[str, Any], username: str) -> dict[str, Any] | None:
        for user in data["users"]:
            if self._same_username(str(user.get("username", "")), username):
                return user
        return None

    def _validate_username(self, username: str) -> str:
        normalized = username.strip()
        if not normalized:
            raise ValueError("Username is required.")
        if any(char.isspace() for char in normalized):
            raise ValueError("Username must not contain whitespace.")
        return normalized

    @staticmethod
    def _same_username(left: str, right: str) -> bool:
        return left.casefold() == right.casefold()

    @staticmethod
    def _public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": str(user["username"]),
            "role": str(user.get("role", SUBACCOUNT_ROLE)),
            "menus": list(sanitize_subaccount_menus(user.get("menus", []))),
            "enabled": bool(user.get("enabled", True)),
            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at"),
        }
