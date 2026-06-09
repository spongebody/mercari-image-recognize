# Console Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser-native HTTP Basic auth popup with a custom modern login page (username + password) gated by a signed-cookie session, protecting all console pages.

**Architecture:** A new `/login` page POSTs credentials to `/api/v1/console/login`, which validates `LOGS_USER` + `LOGS_PASSWORD` and sets an HttpOnly signed-cookie session (`console_session`). The shared auth dependency `require_logs_auth` additionally accepts that cookie (keeping backward-compatible Bearer/Basic for APIs). Console page routes redirect unauthenticated visitors to `/login` instead of returning 401.

**Tech Stack:** Python 3, FastAPI, standard-library `hmac`/`hashlib`/`base64` (no new deps), pytest + Starlette `TestClient`, vanilla HTML/CSS/JS frontend.

**Reference spec:** `docs/superpowers/specs/2026-06-09-console-login-design.md`

**Conventions captured from the codebase:**
- Config fields live in `app/config.py` as dataclass fields using `os.getenv` (see `logs_password` at `app/config.py:129`).
- Auth helper is `app/observability/auth.py::require_logs_auth`.
- Console page + protected-API routes are in `main.py` (`/`, `/config`, `/evaluations`, `/logs`).
- Tests reload modules with env via the `_reload_with_password` pattern in `tests/test_console_routes.py`.
- UI language key is `localStorage["mercari_ui_lang"]` (default `"zh"`).
- Endpoints follow the `/api/v1/...` prefix, so login/logout use `/api/v1/console/login` and `/api/v1/console/logout`.

---

### Task 1: Session-token helpers + `LOGS_USER` config

**Files:**
- Modify: `app/config.py` (add `logs_user` field next to `logs_password`, ~line 129)
- Modify: `app/observability/auth.py` (add constants + token helpers)
- Test: `tests/test_console_session.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_console_session.py`:

```python
import time

from app.observability.auth import (
    COOKIE_NAME,
    REMEMBER_TTL,
    SESSION_TTL,
    make_session_token,
    verify_session_token,
)


def test_cookie_name_and_ttls():
    assert COOKIE_NAME == "console_session"
    assert REMEMBER_TTL == 30 * 24 * 3600
    assert SESSION_TTL == 12 * 3600


def test_token_roundtrip_valid():
    token = make_session_token("hunter2", 3600)
    assert verify_session_token(token, "hunter2") is True


def test_token_rejected_with_wrong_password():
    token = make_session_token("hunter2", 3600)
    assert verify_session_token(token, "other") is False


def test_token_rejected_when_expired():
    token = make_session_token("hunter2", -1)
    assert verify_session_token(token, "hunter2") is False


def test_token_rejected_when_tampered():
    token = make_session_token("hunter2", 3600)
    payload, _, sig = token.partition(".")
    tampered = payload + "x." + sig
    assert verify_session_token(tampered, "hunter2") is False


def test_token_rejected_when_password_empty():
    assert verify_session_token(make_session_token("", 3600), "") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_console_session.py -v`
Expected: FAIL with `ImportError: cannot import name 'COOKIE_NAME'`.

- [ ] **Step 3: Add token helpers to `app/observability/auth.py`**

At the top of `app/observability/auth.py`, extend the imports and add helpers/constants. The current imports are:

```python
from __future__ import annotations

import base64
import hmac
from typing import Callable, Optional

from fastapi import Header, HTTPException
```

Replace that import block with:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Callable, Optional

from fastapi import Cookie, Header, HTTPException, Request

COOKIE_NAME = "console_session"
REMEMBER_TTL = 30 * 24 * 3600  # 30 days
SESSION_TTL = 12 * 3600        # 12 hours


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def make_session_token(password: str, ttl_seconds: int) -> str:
    """Return a signed `<payload>.<sig>` session token bound to `password`."""
    payload = _b64url(json.dumps({"exp": int(time.time()) + ttl_seconds}).encode())
    sig = _b64url(hmac.new(password.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_session_token(token: Optional[str], password: str) -> bool:
    if not password or not token or "." not in token:
        return False
    payload, _, sig = token.partition(".")
    expected = _b64url(hmac.new(password.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        data = json.loads(_b64url_decode(payload))
    except Exception:
        return False
    return int(data.get("exp", 0)) > int(time.time())
```

- [ ] **Step 4: Add `logs_user` field to `app/config.py`**

Find this line (`app/config.py:129`):

```python
    logs_password: str = field(default_factory=lambda: os.getenv("LOGS_PASSWORD", ""))
```

Add directly below it:

```python
    logs_user: str = field(default_factory=lambda: os.getenv("LOGS_USER", "admin"))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_console_session.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/observability/auth.py tests/test_console_session.py
git commit -m "feat: add LOGS_USER config and signed session-token helpers"
```

---

### Task 2: Accept `console_session` cookie in the auth dependency

**Files:**
- Modify: `app/observability/auth.py` (`require_logs_auth`, add `is_console_authed`)
- Test: `tests/test_observability_auth.py` (append cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_observability_auth.py`:

```python
def test_valid_session_cookie_accepted():
    from app.observability.auth import COOKIE_NAME, make_session_token

    client = TestClient(_app("hunter2"))
    client.cookies.set(COOKIE_NAME, make_session_token("hunter2", 3600))
    r = client.get("/secret")
    assert r.status_code == 200


def test_invalid_session_cookie_rejected():
    from app.observability.auth import COOKIE_NAME

    client = TestClient(_app("hunter2"))
    client.cookies.set(COOKIE_NAME, "bogus.token")
    r = client.get("/secret")
    assert r.status_code == 401


def test_is_console_authed_via_cookie_and_header():
    import base64 as _b64

    from fastapi import Request

    from app.observability.auth import COOKIE_NAME, is_console_authed, make_session_token

    def _request(headers):
        scope = {"type": "http", "headers": headers}
        return Request(scope)

    cookie = make_session_token("hunter2", 3600)
    req_cookie = _request([(b"cookie", f"{COOKIE_NAME}={cookie}".encode())])
    assert is_console_authed(req_cookie, "hunter2") is True

    basic = _b64.b64encode(b"a:hunter2").decode()
    req_header = _request([(b"authorization", f"Basic {basic}".encode())])
    assert is_console_authed(req_header, "hunter2") is True

    req_none = _request([])
    assert is_console_authed(req_none, "hunter2") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_observability_auth.py -v`
Expected: FAIL (`test_valid_session_cookie_accepted` returns 401; `is_console_authed` import error).

- [ ] **Step 3: Refactor `require_logs_auth` and add `is_console_authed`**

In `app/observability/auth.py`, replace the entire existing `require_logs_auth` function with:

```python
def _password_from_header(authorization: str) -> Optional[str]:
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(value).decode("utf-8", errors="replace")
            _, _, pw = decoded.partition(":")
            return pw
        except Exception:
            return None
    if scheme.lower() == "bearer":
        return value
    return None


def _is_authed(authorization: Optional[str], cookie: Optional[str], password: str) -> bool:
    if cookie and verify_session_token(cookie, password):
        return True
    if authorization:
        provided = _password_from_header(authorization)
        if provided and hmac.compare_digest(provided, password):
            return True
    return False


def is_console_authed(request: Request, password: str) -> bool:
    """True if the request carries a valid session cookie or auth header."""
    if not password:
        return False
    return _is_authed(
        request.headers.get("authorization"),
        request.cookies.get(COOKIE_NAME),
        password,
    )


def require_logs_auth(expected_password: str) -> Callable:
    def _dep(
        authorization: Optional[str] = Header(default=None),
        console_session: Optional[str] = Cookie(default=None),
    ) -> None:
        if not expected_password:
            raise HTTPException(status_code=503, detail="Logs viewer not configured (set LOGS_PASSWORD).")
        if _is_authed(authorization, console_session, expected_password):
            return
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="logs"'},
        )

    return _dep
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_observability_auth.py -v`
Expected: PASS (all original + 3 new cases).

- [ ] **Step 5: Commit**

```bash
git add app/observability/auth.py tests/test_observability_auth.py
git commit -m "feat: accept console_session cookie in require_logs_auth"
```

---

### Task 3: Login / logout API endpoints

**Files:**
- Modify: `main.py` (imports; add two routes)
- Test: `tests/test_console_login_api.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_console_login_api.py`:

```python
import importlib

import app.config
import main
from app.observability.auth import COOKIE_NAME
from fastapi.testclient import TestClient


def _reload(monkeypatch, user="admin", password="secret"):
    monkeypatch.setenv("LOGS_USER", user)
    monkeypatch.setenv("LOGS_PASSWORD", password)
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def test_login_success_sets_cookie(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "admin", "password": "secret", "remember": True})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert COOKIE_NAME in r.cookies


def test_login_wrong_password_401_no_cookie(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "admin", "password": "nope", "remember": False})
        assert r.status_code == 401
        assert COOKIE_NAME not in r.cookies


def test_login_wrong_username_401(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "root", "password": "secret", "remember": False})
        assert r.status_code == 401


def test_login_not_configured_503(monkeypatch):
    m = _reload(monkeypatch, password="")
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "admin", "password": "x", "remember": False})
        assert r.status_code == 503


def test_remember_false_has_no_max_age(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "admin", "password": "secret", "remember": False})
        set_cookie = r.headers["set-cookie"]
        assert "Max-Age" not in set_cookie


def test_logout_clears_cookie(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/logout")
        assert r.status_code == 200
        assert 'console_session=' in r.headers["set-cookie"]
        assert "Max-Age=0" in r.headers["set-cookie"] or "expires=" in r.headers["set-cookie"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_console_login_api.py -v`
Expected: FAIL with 404 (routes not defined).

- [ ] **Step 3: Add imports to `main.py`**

The current line 11 and 14 are:

```python
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
...
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
```

Change line 11 to add `Response`:

```python
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
```

Change line 14 to add `RedirectResponse`:

```python
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
```

Add to the auth import (`main.py:33`, currently `from app.observability.auth import require_logs_auth`):

```python
from app.observability.auth import (
    COOKIE_NAME,
    REMEMBER_TTL,
    SESSION_TTL,
    is_console_authed,
    make_session_token,
    require_logs_auth,
)
```

Also add `import hmac` near the other stdlib imports at the top of `main.py` (only if not already imported — check the import block first; add it if missing).

- [ ] **Step 4: Add login/logout routes to `main.py`**

Insert these routes immediately after the `index_page` route (after the `return FileResponse(index_path)` block, before `@app.get("/favicon.ico")`):

```python
LOGIN_PAGE_PATH = WEB_DIR / "login.html"


@app.post("/api/v1/console/login")
def console_login(payload: Dict[str, Any], request: Request, response: Response) -> Dict[str, Any]:
    if not settings.logs_password:
        raise HTTPException(status_code=503, detail="Login not configured (set LOGS_PASSWORD).")
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    remember = bool(payload.get("remember", False))
    user_ok = hmac.compare_digest(username, settings.logs_user)
    pass_ok = hmac.compare_digest(password, settings.logs_password)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    ttl = REMEMBER_TTL if remember else SESSION_TTL
    token = make_session_token(settings.logs_password, ttl)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=REMEMBER_TTL if remember else None,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return {"ok": True}


@app.post("/api/v1/console/logout")
def console_logout(response: Response) -> Dict[str, Any]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_console_login_api.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_console_login_api.py
git commit -m "feat: add console login/logout endpoints"
```

---

### Task 4: Build the login page `web/login.html`

**Files:**
- Create: `web/login.html`

This is the confirmed **style A** (light, matches console) with username + password, show/hide password, remember-me, zh/ja toggle, and inline error states (no native alert).

- [ ] **Step 1: Create `web/login.html`**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mercari コンソール · ログイン</title>
<style>
  :root{--accent:#2f80ed;--border:#e5e7eb;--text:#1c1f25;--muted:#6b7280;--danger:#dc2626}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
    font-family:"Inter","Noto Sans JP",system-ui,-apple-system,sans-serif;color:var(--text);
    background:radial-gradient(circle at 12% 12%,#e0e7ff 0,transparent 30%),
      radial-gradient(circle at 88% 90%,#dbeafe 0,transparent 30%),#f7f8fa}
  .card{width:360px;background:#fff;border:1px solid var(--border);border-radius:18px;
    padding:32px 30px 26px;box-shadow:0 18px 44px -22px rgba(31,41,55,.45);position:relative}
  .logo{width:50px;height:50px;border-radius:15px;background:linear-gradient(135deg,var(--accent),#1d4ed8);
    display:flex;align-items:center;justify-content:center;margin:0 auto;box-shadow:0 8px 18px -6px rgba(47,128,237,.55)}
  .logo svg{width:26px;height:26px;stroke:#fff;fill:none;stroke-width:2}
  h2{text-align:center;font-size:17px;margin:14px 0 2px}
  .sub{text-align:center;font-size:12.5px;color:var(--muted);margin:0 0 22px}
  .lbl{display:block;font-size:12.5px;font-weight:600;color:#374151;margin:0 0 6px 2px}
  .field{position:relative;margin-bottom:15px}
  .field .ic{position:absolute;left:13px;top:50%;transform:translateY(-50%);width:18px;height:18px;fill:none;stroke:#9ca3af;stroke-width:2}
  .inp{width:100%;height:46px;border:1.5px solid var(--border);border-radius:11px;background:#fdfdfd;
    font-size:14.5px;font-family:inherit;color:var(--text);padding:0 44px 0 40px;transition:.15s}
  .inp:focus{outline:none;border-color:var(--accent);background:#fff;box-shadow:0 0 0 4px rgba(47,128,237,.12)}
  .field.err .inp{border-color:var(--danger);background:#fef2f2}
  .shake{animation:shake .45s}
  @keyframes shake{10%,90%{transform:translateX(-1px)}20%,80%{transform:translateX(2px)}
    30%,50%,70%{transform:translateX(-4px)}40%,60%{transform:translateX(4px)}}
  .eye{position:absolute;right:8px;top:50%;transform:translateY(-50%);border:none;background:none;cursor:pointer;padding:8px;color:#9ca3af}
  .eye svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2}
  .rem{display:flex;align-items:center;gap:8px;font-size:12.5px;color:#4b5563;margin:2px 0 20px;cursor:pointer;user-select:none}
  .rem input{width:15px;height:15px;accent-color:var(--accent)}
  .btn{width:100%;height:46px;border:none;border-radius:11px;background:linear-gradient(135deg,var(--accent),#1d4ed8);
    color:#fff;font-size:14.5px;font-weight:600;font-family:inherit;letter-spacing:.3px;cursor:pointer;
    display:flex;align-items:center;justify-content:center;gap:8px;box-shadow:0 9px 20px -9px rgba(47,128,237,.7)}
  .btn[disabled]{opacity:.85;cursor:default}
  .spin{width:17px;height:17px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;animation:sp .7s linear infinite}
  @keyframes sp{to{transform:rotate(360deg)}}
  .alert{display:none;align-items:center;gap:7px;margin-top:15px;padding:10px 12px;background:#fef2f2;
    border:1px solid #fecaca;border-radius:10px;color:var(--danger);font-size:12.5px;font-weight:500}
  .alert.show{display:flex}
  .alert svg{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:2}
  .lang{text-align:center;font-size:11.5px;color:#9ca3af;margin-top:18px}
  .lang span{cursor:pointer} .lang span.active{color:var(--accent);font-weight:600}
</style>
</head>
<body>
  <form class="card" id="form" autocomplete="on">
    <div class="logo"><svg viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></div>
    <h2 id="t-title">Mercari コンソール</h2>
    <div class="sub" id="t-sub">商品画像識別テスター</div>

    <label class="lbl" id="t-user-label">用户名</label>
    <div class="field" id="f-user">
      <svg class="ic" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>
      <input class="inp" style="padding-right:14px" id="username" name="username" type="text" autocomplete="username">
    </div>

    <label class="lbl" id="t-pass-label">密码</label>
    <div class="field" id="f-pass">
      <svg class="ic" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      <input class="inp" id="password" name="password" type="password" autocomplete="current-password">
      <button class="eye" type="button" id="toggle" aria-label="toggle">
        <svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
      </button>
    </div>

    <label class="rem"><input type="checkbox" id="remember" checked> <span id="t-remember">记住我（30 天免登录）</span></label>
    <button class="btn" id="submit" type="submit"><span id="t-btn">登 录</span></button>

    <div class="alert" id="alert">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
      <span id="t-error">用户名或密码错误，请重试</span>
    </div>

    <div class="lang"><span data-lang="ja" id="l-ja">日本語</span> / <span data-lang="zh" id="l-zh">中文</span></div>
  </form>

<script>
  const I18N = {
    zh: {title:"Mercari コンソール", sub:"商品图片识别测试", user:"用户名", pass:"密码",
         remember:"记住我（30 天免登录）", btn:"登 录", loading:"登录中…",
         err:"用户名或密码错误，请重试", notConfigured:"登录未配置（请设置 LOGS_PASSWORD）"},
    ja: {title:"Mercari コンソール", sub:"商品画像識別テスター", user:"ユーザー名", pass:"パスワード",
         remember:"ログイン状態を保持（30日）", btn:"ログイン", loading:"ログイン中…",
         err:"ユーザー名またはパスワードが正しくありません", notConfigured:"ログイン未設定（LOGS_PASSWORD を設定してください）"}
  };
  let lang = localStorage.getItem("mercari_ui_lang") || "zh";

  function applyLang(){
    const t = I18N[lang];
    document.documentElement.lang = lang === "ja" ? "ja" : "zh";
    document.getElementById("t-title").textContent = t.title;
    document.getElementById("t-sub").textContent = t.sub;
    document.getElementById("t-user-label").textContent = t.user;
    document.getElementById("t-pass-label").textContent = t.pass;
    document.getElementById("username").placeholder = t.user;
    document.getElementById("password").placeholder = t.pass;
    document.getElementById("t-remember").textContent = t.remember;
    document.getElementById("t-btn").textContent = t.btn;
    document.getElementById("l-zh").classList.toggle("active", lang === "zh");
    document.getElementById("l-ja").classList.toggle("active", lang === "ja");
  }
  document.querySelectorAll(".lang span").forEach(el =>
    el.addEventListener("click", () => {
      lang = el.dataset.lang;
      localStorage.setItem("mercari_ui_lang", lang);
      applyLang();
    }));
  applyLang();

  // Show/hide password
  document.getElementById("toggle").addEventListener("click", () => {
    const p = document.getElementById("password");
    p.type = p.type === "password" ? "text" : "password";
  });

  // Sanitize ?next= to a same-origin path only (avoid open redirect)
  function safeNext(){
    const raw = new URLSearchParams(location.search).get("next") || "/";
    if (raw.startsWith("/") && !raw.startsWith("//")) return raw;
    return "/";
  }

  const form = document.getElementById("form");
  const alertBox = document.getElementById("alert");
  const fUser = document.getElementById("f-user");
  const fPass = document.getElementById("f-pass");
  const submit = document.getElementById("submit");

  function showError(msg){
    document.getElementById("t-error").textContent = msg;
    alertBox.classList.add("show");
    fUser.classList.add("err"); fPass.classList.add("err");
    fPass.classList.remove("shake"); void fPass.offsetWidth; fPass.classList.add("shake");
    document.getElementById("username").focus();
  }
  function clearError(){
    alertBox.classList.remove("show");
    fUser.classList.remove("err"); fPass.classList.remove("err");
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearError();
    const t = I18N[lang];
    const body = {
      username: document.getElementById("username").value,
      password: document.getElementById("password").value,
      remember: document.getElementById("remember").checked,
    };
    submit.disabled = true;
    submit.innerHTML = '<span class="spin"></span> ' + t.loading;
    try {
      const r = await fetch("/api/v1/console/login", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
      if (r.ok) { location.replace(safeNext()); return; }
      showError(r.status === 503 ? t.notConfigured : t.err);
    } catch (err) {
      showError(t.err);
    }
    submit.disabled = false;
    submit.innerHTML = '<span id="t-btn">' + t.btn + '</span>';
  });
</script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add web/login.html
git commit -m "feat: add console login page (style A)"
```

---

### Task 5: Serve `/login` and redirect unauthenticated console pages

**Files:**
- Modify: `main.py` (add `GET /login`; change `/`, `/config`, `/evaluations`, `/logs`)
- Test: `tests/test_console_login_api.py` (append), `tests/test_console_routes.py` (update 2 existing assertions)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_console_login_api.py`:

```python
def test_login_page_served_without_auth(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.get("/login")
        assert r.status_code == 200
        assert "console/login" in r.text  # the page posts to the login endpoint


def test_unauthenticated_index_redirects_to_login(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")


def test_unauthenticated_config_redirects_to_login(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.get("/config", follow_redirects=False)
        assert r.status_code == 302
        assert "next=" in r.headers["location"]


def test_session_cookie_grants_index_access(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        login = client.post("/api/v1/console/login",
                            json={"username": "admin", "password": "secret", "remember": True})
        assert login.status_code == 200
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200


def test_authed_user_visiting_login_is_redirected_home(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        client.post("/api/v1/console/login",
                    json={"username": "admin", "password": "secret", "remember": True})
        r = client.get("/login", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/"
```

Update the two existing assertions in `tests/test_console_routes.py`. Replace `test_index_requires_password`:

```python
def test_index_requires_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")
```

Replace `test_evaluations_requires_password`:

```python
def test_evaluations_requires_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/evaluations", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_console_login_api.py tests/test_console_routes.py -v`
Expected: FAIL (`/login` 404; `/` still returns 401 not 302).

- [ ] **Step 3: Add `GET /login` route in `main.py`**

Add immediately after the `console_logout` route from Task 3:

```python
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if settings.logs_password and is_console_authed(request, settings.logs_password):
        return RedirectResponse("/", status_code=302)
    if not LOGIN_PAGE_PATH.exists():
        raise HTTPException(status_code=404, detail="Login page not found.")
    return HTMLResponse(LOGIN_PAGE_PATH.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Convert the four console page routes to redirect**

Replace the `index_page` route. Its decorator currently carries `dependencies=[Depends(require_logs_auth(settings.logs_password))]` — remove that and gate inside the function:

```python
@app.get("/", response_class=HTMLResponse)
def index_page(request: Request):
    """Serve the test UI from the same origin as the API."""
    if not is_console_authed(request, settings.logs_password):
        return RedirectResponse("/login?next=/", status_code=302)
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Test UI not found.")
    return FileResponse(index_path)
```

Replace `config_page`:

```python
@app.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    if not is_console_authed(request, settings.logs_password):
        return RedirectResponse("/login?next=/config", status_code=302)
    try:
        return HTMLResponse(CONFIG_PAGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Config page not found.") from exc
```

Replace `evaluations_page`:

```python
@app.get("/evaluations", response_class=HTMLResponse)
def evaluations_page(request: Request):
    if not is_console_authed(request, settings.logs_password):
        return RedirectResponse("/login?next=/evaluations", status_code=302)
    try:
        return HTMLResponse(EVALUATIONS_PAGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Evaluations page not found.") from exc
```

Replace `logs_page`:

```python
@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    if not is_console_authed(request, settings.logs_password):
        return RedirectResponse("/login?next=/logs", status_code=302)
    return FileResponse(BASE_DIR / "web" / "logs.html")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_console_login_api.py tests/test_console_routes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_console_login_api.py tests/test_console_routes.py
git commit -m "feat: serve /login and redirect unauthenticated console pages"
```

---

### Task 6: Add a "logout" control to the console header

**Files:**
- Modify: `web/index.html`

- [ ] **Step 1: Find the header insertion point**

Run: `grep -n 'mercari_ui_lang\|<h1\|id="lang' web/index.html | head`
Locate the top header / language-switch area (around the `<h1>` near line 54 / the lang control near line 2570).

- [ ] **Step 2: Add a logout button near the language switch**

Insert this button markup adjacent to the existing language switch / header controls (pick the existing top-bar container):

```html
<button id="logout-btn" type="button"
  style="margin-left:12px;height:32px;padding:0 14px;border:1px solid #e5e7eb;border-radius:8px;
         background:#fff;color:#374151;font-size:13px;font-family:inherit;cursor:pointer">
  退出登录 / ログアウト
</button>
```

- [ ] **Step 3: Wire the logout handler**

Add this script near the other inline scripts (after the language setup, e.g. near `web/index.html:2570`):

```html
<script>
  (function(){
    var btn = document.getElementById("logout-btn");
    if (btn) btn.addEventListener("click", async function(){
      try { await fetch("/api/v1/console/logout", {method:"POST", credentials:"same-origin"}); }
      finally { location.replace("/login"); }
    });
  })();
</script>
```

- [ ] **Step 4: Manual verification**

Run: `LOGS_PASSWORD=secret LOGS_USER=admin ./run.sh` (or the project's dev launch), then in a browser:
1. Visit `/` → redirected to `/login`.
2. Log in with `admin` / `secret` → lands on the test UI.
3. Click "退出登录" → returns to `/login`; visiting `/` again redirects to `/login`.

Expected: all three behave as described; no native browser popup appears.

- [ ] **Step 5: Commit**

```bash
git add web/index.html
git commit -m "feat: add logout control to console header"
```

---

### Task 7: Documentation & env example

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Update `.env.example`**

Run: `grep -n "LOGS_PASSWORD" .env.example` to find the existing line. Directly below `LOGS_PASSWORD`, add:

```bash
# Console login username (paired with LOGS_PASSWORD). Defaults to "admin" if unset.
LOGS_USER=admin
```

If `LOGS_PASSWORD` has an inline comment, update it to note it is now the console login password.

- [ ] **Step 2: Update `README.md`**

Run: `grep -n "LOGS_PASSWORD\|登录\|登陆\|Basic\|鉴权\|认证" README.md` to find the access/auth section. Add or update a short note:

```markdown
### 控制台登录

控制台页面（`/`、`/config`、`/evaluations`、`/logs`）需要登录后访问：

- 用户名：环境变量 `LOGS_USER`（默认 `admin`）
- 密码：环境变量 `LOGS_PASSWORD`

访问任意控制台页面会跳转到 `/login`，登录成功后回到目标页面。勾选「记住我」可保持 30 天免登录。
程序化访问仍可使用 `Authorization: Bearer <LOGS_PASSWORD>`。
```

- [ ] **Step 3: Commit**

```bash
git add .env.example README.md
git commit -m "docs: document LOGS_USER and console login"
```

---

### Final verification

- [ ] **Run the full auth/console test set**

Run: `python -m pytest tests/test_console_session.py tests/test_observability_auth.py tests/test_console_login_api.py tests/test_console_routes.py -v`
Expected: all PASS.

- [ ] **Run the full suite to catch regressions**

Run: `python -m pytest -q`
Expected: no new failures versus the pre-change baseline.

---

## Self-Review Notes

- **Spec coverage:** username+password validation (Task 3), `LOGS_USER` default admin (Task 1), signed cookie + remember/session TTL (Task 1+3), cookie accepted by `require_logs_auth` (Task 2), all four pages redirect to `/login` (Task 5), `/login` page + already-authed redirect (Task 5), login page UI with three states/show-hide/i18n/inline-error (Task 4), logout control (Task 6), backward-compatible Bearer/Basic (Task 2 tests), `next` open-redirect guard (Task 4 `safeNext`), docs/env (Task 7), tests for all (every task). All spec sections mapped.
- **Endpoint path note:** spec wrote `/api/console/...`; plan uses `/api/v1/console/...` to match the codebase's `/api/v1` convention. Frontend (`login.html`) and logout handler use the same paths — consistent.
- **Type/name consistency:** `COOKIE_NAME`, `make_session_token`, `verify_session_token`, `is_console_authed`, `REMEMBER_TTL`, `SESSION_TTL` defined in Task 1–2 and used identically in Tasks 3 & 5.
- **Existing-test impact:** Task 5 updates the two `test_console_routes.py` assertions that previously expected 401 for unauthenticated pages (now 302 → `/login`); pages served with a Basic header still return 200 (covered by `is_console_authed`).
