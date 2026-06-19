# Console Account Menu Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add local subaccount management and menu-level permissions while keeping the `.env` account as the only superadmin.

**Architecture:** Store subaccounts in `data/console_users.json`, hash subaccount passwords with standard-library PBKDF2, and extend the existing signed console session cookie to carry identity and menu claims. Enforce permissions twice: filter menus in `web/assets/shell.js`, and protect page routes plus menu-owned APIs in FastAPI.

**Tech Stack:** FastAPI, pytest, vanilla JavaScript/HTML/CSS, Python standard library (`hashlib`, `hmac`, `secrets`, `json`, `os.replace`).

---

## File Structure

- Create `app/console_accounts.py`: owns menu constants, password hashing, JSON file read/write, subaccount CRUD, and subaccount authentication.
- Modify `app/observability/auth.py`: add `ConsoleIdentity`, payload-aware token creation/verification, identity extraction from cookie/header, and menu permission dependencies.
- Modify `main.py`: instantiate `ConsoleAccountStore`, update login/logout/me routes, add account CRUD API, add `/accounts`, and require menu permissions for console pages and APIs.
- Create `web/accounts.html`: superadmin-only account management UI.
- Modify `web/assets/shell.js`: load `/api/v1/console/me`, filter top-level menus, and show current user.
- Modify `web/assets/shell.css`: small topbar user styles if needed.
- Test `tests/test_console_accounts.py`: account store, hashing, file behavior.
- Modify `tests/test_console_session.py`: token payload and backwards-compatible legacy token behavior.
- Modify `tests/test_console_login_api.py`: superadmin/subaccount login and `/me`.
- Modify `tests/test_console_routes.py`: page/API menu permission enforcement.
- Create `tests/test_console_accounts_api.py`: account CRUD API behavior.

## Task 1: Account Store And Password Hashing

**Files:**
- Create: `app/console_accounts.py`
- Test: `tests/test_console_accounts.py`

- [ ] **Step 1: Write failing account-store tests**

Add `tests/test_console_accounts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_console_accounts.py -v
```

Expected: FAIL during import because `app.console_accounts` does not exist.

- [ ] **Step 3: Implement account store**

Create `app/console_accounts.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

ALL_MENUS = ("test", "config", "evaluations", "logs", "accounts")
ASSIGNABLE_MENUS = ("test", "config", "evaluations", "logs")
SUPERADMIN_ROLE = "superadmin"
SUBACCOUNT_ROLE = "subaccount"
PBKDF2_ITERATIONS = 260_000


@dataclass(frozen=True)
class ConsoleUser:
    username: str
    menus: tuple[str, ...]
    enabled: bool = True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sanitize_subaccount_menus(menus: Iterable[str]) -> list[str]:
    allowed = set(ASSIGNABLE_MENUS)
    return sorted({str(menu).strip() for menu in menus if str(menu).strip() in allowed})


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = encoded_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


class ConsoleAccountStore:
    def __init__(self, path: Path, *, superadmin_username: str = "admin") -> None:
        self.path = path
        self.superadmin_username = superadmin_username

    def _empty_data(self) -> dict[str, Any]:
        return {"version": 1, "users": []}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            self._write(self._empty_data())
        with self.path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("users"), list):
            raise ValueError("Console users file has an invalid shape.")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)

    def _find(self, data: dict[str, Any], username: str) -> Optional[dict[str, Any]]:
        for user in data["users"]:
            if user.get("username") == username:
                return user
        return None

    def _public_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": user["username"],
            "menus": sanitize_subaccount_menus(user.get("menus", [])),
            "enabled": bool(user.get("enabled", True)),
            "created_at": user.get("created_at", ""),
            "updated_at": user.get("updated_at", ""),
        }

    def list_users(self) -> list[dict[str, Any]]:
        data = self._read()
        return [self._public_user(user) for user in data["users"]]

    def create_user(self, username: str, password: str, menus: Iterable[str], enabled: bool = True) -> dict[str, Any]:
        username = username.strip()
        if not username:
            raise ValueError("Username is required.")
        if username == self.superadmin_username:
            raise ValueError("Username is reserved for the superadmin.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")
        clean_menus = sanitize_subaccount_menus(menus)
        if not clean_menus:
            raise ValueError("Subaccount must have at least one menu.")
        data = self._read()
        if self._find(data, username):
            raise ValueError("User already exists.")
        now = utc_now_iso()
        user = {
            "username": username,
            "password_hash": hash_password(password),
            "menus": clean_menus,
            "enabled": bool(enabled),
            "created_at": now,
            "updated_at": now,
        }
        data["users"].append(user)
        self._write(data)
        return self._public_user(user)

    def update_user(
        self,
        username: str,
        *,
        password: Optional[str] = None,
        menus: Optional[Iterable[str]] = None,
        enabled: Optional[bool] = None,
    ) -> dict[str, Any]:
        data = self._read()
        user = self._find(data, username)
        if user is None:
            raise KeyError(username)
        if password is not None:
            if len(password) < 6:
                raise ValueError("Password must be at least 6 characters.")
            user["password_hash"] = hash_password(password)
        if menus is not None:
            clean_menus = sanitize_subaccount_menus(menus)
            if not clean_menus:
                raise ValueError("Subaccount must have at least one menu.")
            user["menus"] = clean_menus
        if enabled is not None:
            user["enabled"] = bool(enabled)
        user["updated_at"] = utc_now_iso()
        self._write(data)
        return self._public_user(user)

    def delete_user(self, username: str) -> None:
        data = self._read()
        before = len(data["users"])
        data["users"] = [user for user in data["users"] if user.get("username") != username]
        if len(data["users"]) == before:
            raise KeyError(username)
        self._write(data)

    def get_user(self, username: str) -> Optional[ConsoleUser]:
        data = self._read()
        user = self._find(data, username)
        if user is None or not bool(user.get("enabled", True)):
            return None
        menus = tuple(sanitize_subaccount_menus(user.get("menus", [])))
        return ConsoleUser(username=username, menus=menus, enabled=True)

    def authenticate(self, username: str, password: str) -> Optional[ConsoleUser]:
        data = self._read()
        user = self._find(data, username.strip())
        if user is None or not bool(user.get("enabled", True)):
            return None
        if not verify_password(password, str(user.get("password_hash", ""))):
            return None
        menus = tuple(sanitize_subaccount_menus(user.get("menus", [])))
        if not menus:
            return None
        return ConsoleUser(username=username.strip(), menus=menus, enabled=True)
```

- [ ] **Step 4: Run account-store tests to verify GREEN**

Run:

```bash
pytest tests/test_console_accounts.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/console_accounts.py tests/test_console_accounts.py
git commit -m "feat(console): add local account store"
```

## Task 2: Session Identity And Menu Dependencies

**Files:**
- Modify: `app/observability/auth.py`
- Test: `tests/test_console_session.py`

- [ ] **Step 1: Write failing session identity tests**

Append to `tests/test_console_session.py`:

```python
from app.observability.auth import (
    ConsoleIdentity,
    make_identity_session_token,
    verify_session_identity,
)


def test_identity_token_roundtrip_contains_user_role_and_menus():
    token = make_identity_session_token(
        "hunter2",
        ttl_seconds=3600,
        username="model-tester",
        role="subaccount",
        menus=["evaluations"],
    )

    identity = verify_session_identity(token, "hunter2")

    assert identity == ConsoleIdentity(
        username="model-tester",
        role="subaccount",
        menus=("evaluations",),
    )


def test_legacy_exp_only_token_is_superadmin_for_compatibility():
    token = make_session_token("hunter2", 3600)

    identity = verify_session_identity(token, "hunter2")

    assert identity.role == "superadmin"
    assert "accounts" in identity.menus
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_console_session.py -v
```

Expected: FAIL because `ConsoleIdentity`, `make_identity_session_token`, and `verify_session_identity` are missing.

- [ ] **Step 3: Implement identity helpers**

Modify `app/observability/auth.py`:

```python
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

ALL_MENUS = ("test", "config", "evaluations", "logs", "accounts")
SUPERADMIN_ROLE = "superadmin"
SUBACCOUNT_ROLE = "subaccount"


@dataclass(frozen=True)
class ConsoleIdentity:
    username: str
    role: str
    menus: tuple[str, ...]

    @property
    def is_superadmin(self) -> bool:
        return self.role == SUPERADMIN_ROLE

    def has_menu(self, menu_id: str) -> bool:
        return menu_id in self.menus
```

Add payload-aware token creation and verification while keeping existing `make_session_token()` and `verify_session_token()`:

```python
def _sign_payload(payload: str, password: str) -> str:
    return _b64url(hmac.new(password.encode(), payload.encode(), hashlib.sha256).digest())


def _make_token(payload_data: dict, password: str) -> str:
    payload = _b64url(json.dumps(payload_data, separators=(",", ":")).encode())
    return f"{payload}.{_sign_payload(payload, password)}"


def make_session_token(password: str, ttl_seconds: int) -> str:
    return _make_token({"exp": int(time.time()) + ttl_seconds}, password)


def make_identity_session_token(
    password: str,
    ttl_seconds: int,
    *,
    username: str,
    role: str,
    menus: Iterable[str],
) -> str:
    return _make_token(
        {
            "exp": int(time.time()) + ttl_seconds,
            "sub": username,
            "role": role,
            "menus": list(menus),
        },
        password,
    )


def _decode_verified_payload(token: Optional[str], password: str) -> Optional[dict]:
    if not password or not token or "." not in token:
        return None
    payload, _, sig = token.partition(".")
    expected = _sign_payload(payload, password)
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_b64url_decode(payload))
    except Exception:
        return None
    if int(data.get("exp", 0)) <= int(time.time()):
        return None
    return data


def verify_session_token(token: Optional[str], password: str) -> bool:
    return _decode_verified_payload(token, password) is not None


def verify_session_identity(token: Optional[str], password: str) -> Optional[ConsoleIdentity]:
    data = _decode_verified_payload(token, password)
    if data is None:
        return None
    role = str(data.get("role") or SUPERADMIN_ROLE)
    username = str(data.get("sub") or "")
    raw_menus = data.get("menus")
    menus = tuple(menu for menu in (raw_menus or ALL_MENUS) if menu in ALL_MENUS)
    if role == SUPERADMIN_ROLE:
        menus = ALL_MENUS
        username = username or "admin"
    if role == SUBACCOUNT_ROLE:
        menus = tuple(menu for menu in menus if menu != "accounts")
    if not username or not menus:
        return None
    return ConsoleIdentity(username=username, role=role, menus=menus)
```

Add identity extraction and permission dependencies:

```python
def identity_from_request(request: Request, password: str) -> Optional[ConsoleIdentity]:
    if not password:
        return None
    cookie_identity = verify_session_identity(request.cookies.get(COOKIE_NAME), password)
    if cookie_identity:
        return cookie_identity
    authorization = request.headers.get("authorization")
    if authorization:
        provided = _password_from_header(authorization)
        if provided and hmac.compare_digest(provided, password):
            return ConsoleIdentity(username="admin", role=SUPERADMIN_ROLE, menus=ALL_MENUS)
    return None


def require_menu_auth(expected_password: str, menu_id: str) -> Callable:
    def _dep(request: Request) -> ConsoleIdentity:
        if not expected_password:
            raise HTTPException(status_code=503, detail="Console auth not configured (set LOGS_PASSWORD).")
        identity = identity_from_request(request, expected_password)
        if identity is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if not identity.has_menu(menu_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return identity
    return _dep


def require_superadmin_auth(expected_password: str) -> Callable:
    def _dep(request: Request) -> ConsoleIdentity:
        identity = require_menu_auth(expected_password, "accounts")(request)
        if not identity.is_superadmin:
            raise HTTPException(status_code=403, detail="Forbidden")
        return identity
    return _dep
```

Update `_is_authed()` and `is_console_authed()` to delegate to identity helpers for cookies while preserving header compatibility.

- [ ] **Step 4: Run session tests to verify GREEN**

Run:

```bash
pytest tests/test_console_session.py tests/test_console_login_api.py::test_login_success_sets_cookie -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/observability/auth.py tests/test_console_session.py
git commit -m "feat(console): carry identity in sessions"
```

## Task 3: Login And Current User API

**Files:**
- Modify: `main.py`
- Modify: `tests/test_console_login_api.py`

- [ ] **Step 1: Write failing login and `/me` tests**

Append to `tests/test_console_login_api.py`:

```python
def test_me_returns_superadmin_identity(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        client.post("/api/v1/console/login", json={"username": "admin", "password": "secret", "remember": True})

        r = client.get("/api/v1/console/me")

        assert r.status_code == 200
        assert r.json()["role"] == "superadmin"
        assert "accounts" in r.json()["menus"]


def test_subaccount_login_uses_json_store_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    m = _reload(monkeypatch)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login", json={"username": "model-tester", "password": "secret123", "remember": True})
        assert r.status_code == 200

        me = client.get("/api/v1/console/me")
        assert me.status_code == 200
        assert me.json()["username"] == "model-tester"
        assert me.json()["role"] == "subaccount"
        assert me.json()["menus"] == ["evaluations"]
        assert me.json()["defaultPath"] == "/evaluations"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_console_login_api.py -v
```

Expected: FAIL because subaccount login and `/api/v1/console/me` are missing.

- [ ] **Step 3: Wire store and login**

Modify imports in `main.py`:

```python
from app.console_accounts import ALL_MENUS, SUBACCOUNT_ROLE, SUPERADMIN_ROLE, ConsoleAccountStore
from app.observability.auth import (
    identity_from_request,
    make_identity_session_token,
    require_menu_auth,
    require_superadmin_auth,
)
```

Instantiate the store near other globals:

```python
CONSOLE_USERS_PATH = Path(os.getenv("CONSOLE_USERS_PATH", str(BASE_DIR / "data" / "console_users.json")))
console_account_store = ConsoleAccountStore(CONSOLE_USERS_PATH, superadmin_username=settings.logs_user)
```

Add path helper:

```python
MENU_DEFAULT_PATHS = {
    "test": "/",
    "config": "/config",
    "evaluations": "/evaluations",
    "logs": "/logs",
    "accounts": "/accounts",
}


def _default_path(menus: list[str] | tuple[str, ...]) -> str:
    for menu in ("test", "evaluations", "config", "logs", "accounts"):
        if menu in menus:
            return MENU_DEFAULT_PATHS[menu]
    return "/login"
```

Update `console_login()`:

```python
    role = None
    menus = None
    user_ok = hmac.compare_digest(username, settings.logs_user)
    pass_ok = hmac.compare_digest(password, settings.logs_password)
    if user_ok and pass_ok:
        role = SUPERADMIN_ROLE
        menus = list(ALL_MENUS)
    else:
        subaccount = console_account_store.authenticate(username, password)
        if subaccount is not None:
            role = SUBACCOUNT_ROLE
            menus = list(subaccount.menus)
            username = subaccount.username
    if role is None or menus is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = make_identity_session_token(
        settings.logs_password,
        ttl,
        username=username,
        role=role,
        menus=menus,
    )
```

Add `/me`:

```python
@app.get("/api/v1/console/me")
def console_me(request: Request) -> Dict[str, Any]:
    identity = identity_from_request(request, settings.logs_password)
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if identity.role == SUBACCOUNT_ROLE:
        live_user = console_account_store.get_user(identity.username)
        if live_user is None:
            raise HTTPException(status_code=403, detail="Forbidden")
        menus = list(live_user.menus)
    else:
        menus = list(ALL_MENUS)
    return {
        "username": identity.username,
        "role": identity.role,
        "menus": menus,
        "defaultPath": _default_path(menus),
    }
```

- [ ] **Step 4: Run login tests to verify GREEN**

Run:

```bash
pytest tests/test_console_login_api.py tests/test_console_session.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_console_login_api.py
git commit -m "feat(console): support subaccount login"
```

## Task 4: Page And API Permission Enforcement

**Files:**
- Modify: `main.py`
- Modify: `tests/test_console_routes.py`

- [ ] **Step 1: Write failing route permission tests**

Append to `tests/test_console_routes.py`:

```python
def test_subaccount_can_only_access_allowed_page(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_USER", "admin")
    monkeypatch.setenv("LOGS_PASSWORD", "secret")
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    importlib.reload(app.config)
    importlib.reload(main)
    main.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/api/v1/console/login", json={"username": "model-tester", "password": "secret123", "remember": True})
        assert client.get("/evaluations", follow_redirects=False).status_code == 200
        assert client.get("/config", follow_redirects=False).status_code == 403
        assert client.get("/accounts", follow_redirects=False).status_code == 403


def test_subaccount_can_only_call_allowed_api(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_USER", "admin")
    monkeypatch.setenv("LOGS_PASSWORD", "secret")
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    importlib.reload(app.config)
    importlib.reload(main)
    main.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/api/v1/console/login", json={"username": "model-tester", "password": "secret123", "remember": True})
        assert client.get("/api/v1/evaluations").status_code == 200
        assert client.get("/api/v1/config").status_code == 403
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_console_routes.py -v
```

Expected: FAIL because pages and APIs do not enforce menu permissions yet.

- [ ] **Step 3: Add page helpers and route dependencies**

In `main.py`, add:

```python
def _console_identity_for_page(request: Request):
    identity = identity_from_request(request, settings.logs_password)
    if identity and identity.role == SUBACCOUNT_ROLE:
        live_user = console_account_store.get_user(identity.username)
        if live_user is None:
            return None
        return type(identity)(username=identity.username, role=identity.role, menus=live_user.menus)
    return identity


def _require_page_menu(request: Request, menu_id: str, next_path: str):
    identity = _console_identity_for_page(request)
    if identity is None:
        return RedirectResponse(f"/login?next={next_path}", status_code=302)
    if not identity.has_menu(menu_id):
        return HTMLResponse("Forbidden", status_code=403)
    return None
```

Update page handlers:

```python
blocked = _require_page_menu(request, "test", "/")
if blocked:
    return blocked
```

Use menu IDs:

- `/` -> `test`
- `/config` -> `config`
- `/evaluations` -> `evaluations`
- `/logs` -> `logs`
- `/accounts` -> `accounts`

Add dependencies:

```python
config_auth = require_menu_auth(settings.logs_password, "config")
evaluation_auth = require_menu_auth(settings.logs_password, "evaluations")
logs_auth = require_menu_auth(settings.logs_password, "logs")
accounts_auth = require_superadmin_auth(settings.logs_password)
```

Apply:

- `@app.get("/api/v1/config", dependencies=[Depends(config_auth)])`
- `@app.get("/api/v1/prompts", dependencies=[Depends(config_auth)])`
- existing config write endpoints use `config_auth`
- every endpoint whose path starts with `/api/v1/evaluations` uses `evaluation_auth`
- `/api/v1/image-proxy` uses `evaluation_auth`
- the existing `build_obs_router` call passes `auth_dep=logs_auth`

- [ ] **Step 4: Run route tests to verify GREEN**

Run:

```bash
pytest tests/test_console_routes.py tests/test_console_login_api.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_console_routes.py
git commit -m "feat(console): enforce menu permissions"
```

## Task 5: Account CRUD API

**Files:**
- Modify: `main.py`
- Test: `tests/test_console_accounts_api.py`

- [ ] **Step 1: Write failing account API tests**

Create `tests/test_console_accounts_api.py`:

```python
import importlib

from fastapi.testclient import TestClient

import app.config
import main


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_USER", "admin")
    monkeypatch.setenv("LOGS_PASSWORD", "secret")
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def _login_admin(client):
    return client.post("/api/v1/console/login", json={"username": "admin", "password": "secret", "remember": True})


def test_superadmin_can_create_update_delete_subaccount(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        _login_admin(client)
        created = client.post("/api/v1/console/users", json={
            "username": "model-tester",
            "password": "secret123",
            "menus": ["evaluations", "accounts"],
            "enabled": True,
        })
        assert created.status_code == 200
        assert created.json()["menus"] == ["evaluations"]

        listed = client.get("/api/v1/console/users")
        assert listed.status_code == 200
        assert listed.json()["users"][0]["username"] == "model-tester"
        assert "password_hash" not in listed.text

        updated = client.put("/api/v1/console/users/model-tester", json={"menus": ["test"], "enabled": False})
        assert updated.status_code == 200
        assert updated.json()["menus"] == ["test"]
        assert updated.json()["enabled"] is False

        deleted = client.delete("/api/v1/console/users/model-tester")
        assert deleted.status_code == 200
        assert client.get("/api/v1/console/users").json()["users"] == []


def test_subaccount_cannot_call_account_api(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])
    with TestClient(m.app) as client:
        client.post("/api/v1/console/login", json={"username": "model-tester", "password": "secret123", "remember": True})
        assert client.get("/api/v1/console/users").status_code == 403
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_console_accounts_api.py -v
```

Expected: FAIL because account CRUD routes do not exist.

- [ ] **Step 3: Implement CRUD API**

Add routes to `main.py`:

```python
@app.get("/api/v1/console/users", dependencies=[Depends(accounts_auth)])
def list_console_users() -> Dict[str, Any]:
    return {"users": console_account_store.list_users()}


@app.post("/api/v1/console/users", dependencies=[Depends(accounts_auth)])
def create_console_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return console_account_store.create_user(
            str(payload.get("username", "")),
            str(payload.get("password", "")),
            payload.get("menus", []),
            bool(payload.get("enabled", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/v1/console/users/{username}", dependencies=[Depends(accounts_auth)])
def update_console_user(username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return console_account_store.update_user(
            username,
            password=payload.get("password") if payload.get("password") else None,
            menus=payload.get("menus") if "menus" in payload else None,
            enabled=bool(payload["enabled"]) if "enabled" in payload else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="User not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/v1/console/users/{username}", dependencies=[Depends(accounts_auth)])
def delete_console_user(username: str) -> Dict[str, Any]:
    try:
        console_account_store.delete_user(username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="User not found.") from exc
    return {"ok": True}
```

- [ ] **Step 4: Run account API tests to verify GREEN**

Run:

```bash
pytest tests/test_console_accounts_api.py tests/test_console_accounts.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_console_accounts_api.py
git commit -m "feat(console): add account management api"
```

## Task 6: Account Management Page

**Files:**
- Create: `web/accounts.html`
- Modify: `main.py`
- Modify: `tests/test_console_routes.py`

- [ ] **Step 1: Write failing accounts page test**

Append to `tests/test_console_routes.py`:

```python
def test_accounts_page_only_served_to_superadmin(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_USER", "admin")
    monkeypatch.setenv("LOGS_PASSWORD", "secret")
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    importlib.reload(app.config)
    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        client.post("/api/v1/console/login", json={"username": "admin", "password": "secret", "remember": True})
        r = client.get("/accounts", follow_redirects=False)
        assert r.status_code == 200
        assert "账号管理" in r.text
        assert "console/users" in r.text
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_console_routes.py::test_accounts_page_only_served_to_superadmin -v
```

Expected: FAIL because `/accounts` is missing.

- [ ] **Step 3: Add page route**

In `main.py`:

```python
ACCOUNTS_PAGE_PATH = BASE_DIR / "web" / "accounts.html"


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request):
    blocked = _require_page_menu(request, "accounts", "/accounts")
    if blocked:
        return blocked
    try:
        return HTMLResponse(ACCOUNTS_PAGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Accounts page not found.") from exc
```

- [ ] **Step 4: Create account management UI**

Create `web/accounts.html` with:

```html
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>账号管理</title>
  <link rel="stylesheet" href="/assets/shell.css?v=20260619" />
  <style>
    :root { --bg:#f7f8fa; --card:#fff; --text:#1c1f25; --muted:#6b7280; --accent:#2f80ed; --border:#e5e7eb; --danger:#e11d48; --success:#16a34a; }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:"Inter","Noto Sans SC","Noto Sans JP",system-ui,-apple-system,sans-serif; min-height:100vh; }
    .page { max-width:none; margin:0; padding:0; }
    main.shell-main .page-content { max-width:none; }
    .layout { display:grid; grid-template-columns:minmax(320px, 420px) 1fr; gap:16px; align-items:start; }
    .card { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:18px 20px; box-shadow:0 6px 20px rgba(15,23,42,.05); }
    .stack { display:grid; gap:14px; }
    .entry-head { display:flex; align-items:center; justify-content:space-between; gap:12px; }
    .entry-title { font-size:15px; font-weight:800; }
    label { display:block; font-size:13px; font-weight:700; color:var(--muted); margin-bottom:6px; }
    input[type="text"], input[type="password"] { width:100%; padding:11px 14px; border:2px solid var(--border); border-radius:10px; background:#fdfdfd; font-size:14px; font-family:inherit; }
    input:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px rgba(47,128,237,.1); background:white; }
    button { border:0; border-radius:10px; background:var(--accent); color:white; padding:10px 14px; font-weight:800; cursor:pointer; font-family:inherit; }
    button.secondary { background:#eef2ff; color:#1e3a8a; }
    button.danger { background:#fee2e2; color:#991b1b; }
    .menu-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:8px; }
    .check { display:flex; align-items:center; gap:8px; padding:9px 10px; border:1px solid var(--border); border-radius:10px; background:#fff; font-size:13px; font-weight:700; color:#374151; }
    .user-list { display:grid; gap:10px; }
    .user-row { display:grid; gap:8px; border:1px solid var(--border); border-radius:12px; padding:12px; background:#fff; }
    .user-meta { display:flex; flex-wrap:wrap; gap:8px; color:var(--muted); font-size:12px; }
    .pill { display:inline-flex; align-items:center; border-radius:999px; padding:4px 8px; background:#eff6ff; color:#1d4ed8; font-size:12px; font-weight:800; }
    .message { min-height:18px; font-size:13px; font-weight:700; }
    .message.error { color:var(--danger); }
    .message.ok { color:var(--success); }
    @media (max-width: 900px) { .layout { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="page">
    <div class="layout">
      <section class="card stack">
        <div class="entry-head"><span class="entry-title">新建 / 编辑子账号</span></div>
        <input id="editing" type="hidden" />
        <div><label for="username">用户名</label><input id="username" type="text" autocomplete="off" /></div>
        <div><label for="password">密码</label><input id="password" type="password" autocomplete="new-password" placeholder="新建必填，编辑时留空表示不修改" /></div>
        <div>
          <label>菜单权限</label>
          <div class="menu-grid" id="menus"></div>
        </div>
        <label class="check"><input id="enabled" type="checkbox" checked /> 启用账号</label>
        <div class="entry-head">
          <button id="save" type="button">保存账号</button>
          <button class="secondary" id="reset" type="button">清空</button>
        </div>
        <div class="message" id="message"></div>
      </section>
      <section class="card stack">
        <div class="entry-head">
          <span class="entry-title">子账号列表</span>
          <button class="secondary" id="refresh" type="button">刷新</button>
        </div>
        <div class="user-list" id="users">正在读取...</div>
      </section>
    </div>
  </div>
  <script src="/assets/shell.js?v=20260619"></script>
  <script>
    const MENU_LABELS = { test:"测试", config:"配置", evaluations:"模型测试", logs:"日志" };
    const ASSIGNABLE = Object.keys(MENU_LABELS);
    const el = (id) => document.getElementById(id);
    const state = { users: [] };
    function msg(text, type) { el("message").textContent = text; el("message").className = "message " + (type || ""); }
    function selectedMenus() { return ASSIGNABLE.filter((id) => document.querySelector(`[data-menu="${id}"]`).checked); }
    function renderMenuChecks(values = []) {
      el("menus").innerHTML = ASSIGNABLE.map((id) => `<label class="check"><input type="checkbox" data-menu="${id}" ${values.includes(id) ? "checked" : ""}> ${MENU_LABELS[id]}</label>`).join("");
    }
    async function api(url, options = {}) {
      const resp = await fetch(url, { credentials:"same-origin", ...options });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || "请求失败");
      return data;
    }
    function resetForm() {
      el("editing").value = ""; el("username").value = ""; el("username").disabled = false; el("password").value = ""; el("enabled").checked = true; renderMenuChecks([]); msg("", "");
    }
    function editUser(username) {
      const user = state.users.find((u) => u.username === username);
      if (!user) return;
      el("editing").value = user.username; el("username").value = user.username; el("username").disabled = true; el("password").value = ""; el("enabled").checked = Boolean(user.enabled); renderMenuChecks(user.menus || []); msg("", "");
    }
    async function deleteUser(username) {
      if (!window.confirm(`删除子账号 ${username}？`)) return;
      await api(`/api/v1/console/users/${encodeURIComponent(username)}`, { method:"DELETE" });
      await loadUsers(); resetForm(); msg("已删除", "ok");
    }
    function renderUsers() {
      el("users").innerHTML = state.users.length ? state.users.map((u) => `
        <div class="user-row">
          <div class="entry-head"><strong>${u.username}</strong><span class="pill">${u.enabled ? "启用" : "停用"}</span></div>
          <div class="user-meta">${(u.menus || []).map((m) => `<span>${MENU_LABELS[m] || m}</span>`).join("")}</div>
          <div class="user-meta">更新：${u.updated_at || "-"}</div>
          <div><button class="secondary" data-edit="${u.username}" type="button">编辑</button> <button class="danger" data-delete="${u.username}" type="button">删除</button></div>
        </div>`).join("") : "暂无子账号";
      document.querySelectorAll("[data-edit]").forEach((n) => n.addEventListener("click", () => editUser(n.dataset.edit)));
      document.querySelectorAll("[data-delete]").forEach((n) => n.addEventListener("click", () => deleteUser(n.dataset.delete)));
    }
    async function loadUsers() {
      const data = await api("/api/v1/console/users");
      state.users = data.users || [];
      renderUsers();
    }
    async function saveUser() {
      const editing = el("editing").value;
      const body = { username: el("username").value, password: el("password").value, menus: selectedMenus(), enabled: el("enabled").checked };
      try {
        if (editing) {
          if (!body.password) delete body.password;
          await api(`/api/v1/console/users/${encodeURIComponent(editing)}`, { method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
        } else {
          await api("/api/v1/console/users", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
        }
        await loadUsers(); resetForm(); msg("已保存", "ok");
      } catch (err) { msg(err.message || String(err), "error"); }
    }
    renderMenuChecks([]);
    el("save").addEventListener("click", saveUser);
    el("reset").addEventListener("click", resetForm);
    el("refresh").addEventListener("click", () => loadUsers().catch((err) => msg(err.message, "error")));
    Shell.mount({ page:"accounts", defaultRoute:"accounts", sidebar:() => [{ id:"accounts", label:"账号管理" }] });
    loadUsers().catch((err) => msg(err.message, "error"));
  </script>
</body>
</html>
```

- [ ] **Step 5: Run accounts page test to verify GREEN**

Run:

```bash
pytest tests/test_console_routes.py::test_accounts_page_only_served_to_superadmin -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py web/accounts.html tests/test_console_routes.py
git commit -m "feat(console): add account management page"
```

## Task 7: Shell Menu Filtering

**Files:**
- Modify: `web/assets/shell.js`
- Modify: `web/assets/shell.css`
- Test: existing route/API tests plus manual browser verification

- [ ] **Step 1: Record current shell behavior**

Run:

```bash
pytest tests/test_console_routes.py::test_shell_assets_served_without_password -v
```

Expected: PASS before edits.

- [ ] **Step 2: Update shell menu definitions**

In `web/assets/shell.js`, replace `PAGES` with:

```js
const PAGES = [
  { id: 'config',      label: '配置', href: '/config' },
  { id: 'test',        label: '测试', href: '/' },
  { id: 'evaluations', label: '模型测试', href: '/evaluations' },
  { id: 'logs',        label: '日志', href: '/logs' },
  { id: 'accounts',    label: '账号管理', href: '/accounts' },
];

let identity = null;
```

Add:

```js
async function loadIdentity() {
  try {
    const resp = await fetch('/api/v1/console/me', { credentials: 'same-origin' });
    if (resp.ok) identity = await resp.json();
  } catch (err) {
    identity = null;
  }
}

function allowedPages() {
  if (!identity || !Array.isArray(identity.menus)) return PAGES;
  const allowed = new Set(identity.menus);
  return PAGES.filter((p) => allowed.has(p.id));
}
```

Make `mount` async enough for initial filtering. Change the function declaration and add the identity load immediately after `config = cfg;`; keep the rest of the existing `mount` body in its current order:

```js
async function mount(cfg) {
  config = cfg;
  await loadIdentity();
}
```

Use `for (const p of allowedPages())` in `renderSidebar()`.

Add a username pill before logout:

```js
identity ? el('span', { class: 'shell-user', text: identity.username }) : null,
```

- [ ] **Step 3: Add CSS for user pill**

In `web/assets/shell.css`:

```css
.shell-user {
  display: inline-flex;
  align-items: center;
  max-width: 180px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--shell-border, #e5e7eb);
  border-radius: 8px;
  background: #fff;
  color: var(--shell-muted, #6b7280);
  font-size: 12px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

- [ ] **Step 4: Run shell asset test**

Run:

```bash
pytest tests/test_console_routes.py::test_shell_assets_served_without_password -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/assets/shell.js web/assets/shell.css
git commit -m "feat(console): filter shell menu by account"
```

## Task 8: Full Verification And Docs

**Files:**
- Modify: `README.md` or `API.md` only if they contain console login instructions that now need account-management wording.

- [ ] **Step 1: Run targeted test suite**

Run:

```bash
pytest tests/test_console_accounts.py tests/test_console_session.py tests/test_console_login_api.py tests/test_console_routes.py tests/test_console_accounts_api.py -v
```

Expected: PASS.

- [ ] **Step 2: Run broader relevant tests**

Run:

```bash
pytest tests/test_config_api.py tests/test_prompts_api.py tests/test_evaluations_api.py tests/test_observability_api.py -v
```

Expected: PASS. If any test expected unauthenticated access to config/prompts/evaluations, update it to authenticate as superadmin because these APIs are now menu-protected.

- [ ] **Step 3: Manual smoke test**

Start the server:

```bash
uvicorn main:app --reload --port 8000
```

Expected:

- Superadmin logs in with `.env` credentials and sees all menus including「账号管理」.
- Superadmin creates `model-tester` with only「模型测试」.
- `model-tester` logs in and only sees「模型测试」.
- `model-tester` can open `/evaluations`.
- `model-tester` gets 403 for `/config` and `/accounts`.

- [ ] **Step 4: Commit final docs/test adjustments**

```bash
git add README.md API.md tests
git commit -m "docs(console): document account permissions"
```

Skip this commit if no docs or extra test updates were needed.

## Self-Review

- Spec coverage: Tasks cover JSON storage in `data`, password hashing, superadmin from `.env`, subaccount login, `/me`, page/API menu permission checks, account CRUD, accounts UI, shell menu filtering, and verification.
- Scope check: The plan stays at menu-level permissions. It does not add roles beyond `superadmin` and `subaccount`, audit logs, invitations, or a database.
- Type consistency: Menu IDs are consistently `test`, `config`, `evaluations`, `logs`, `accounts`; roles are consistently `superadmin` and `subaccount`; user APIs consistently use `/api/v1/console/users`.
