# 控制台登录界面设计 (Console Login)

- 日期: 2026-06-09
- 状态: 已确认设计，待实现
- 目标: 用现代化的自定义登录页替换当前的 HTTP Basic auth（浏览器原生弹窗），登录后才能进入控制台页面。

## 1. 背景与现状

当前控制台页面 `/`、`/config`、`/evaluations`、`/logs` 以及 observability API
均由 `app/observability/auth.py` 的 `require_logs_auth(LOGS_PASSWORD)` 依赖保护，
采用 **HTTP Basic auth**。未登录访问时浏览器弹出原生凭据对话框（即用户所说的「原生 alert」）。

- 凭据来源: 单一共享密码 `LOGS_PASSWORD`（`app/config.py:129`）。
- 商品识别业务 API（`/api/v1/mercari/...`）**不**受此保护，本次也不改动。
- `web/logs.html` 等页面的 fetch 使用 `credentials:'include'`，依赖浏览器缓存的 Basic 凭据自动带上 `Authorization` 头。

## 2. 目标与范围

### 决策（已与用户确认）

| 项 | 决定 |
|----|------|
| 凭据模型 | **用户名 + 密码**，两者都校验 |
| 用户名来源 | 新增 `LOGS_USER` 环境变量，未配置时默认 `admin` |
| 密码来源 | 复用现有 `LOGS_PASSWORD` |
| 保护范围 | 全部控制台页面：`/`、`/config`、`/evaluations`、`/logs`（含其数据 API） |
| 会话时长 | 持久 Cookie +「记住我」（勾选 30 天，不勾选为会话级） |
| 视觉风格 | 风格 A（浅色，贴合现有控制台）|

### 不在范围内

- 不改动商品识别业务 API 的鉴权。
- 不做多用户/用户表。
- 不做找回密码、注册、验证码等。

## 3. 技术路线

把 HTTP Basic auth 换成 **自定义登录页 + 签名 Cookie 会话**。
服务端真正校验，UI 完全自定义、无浏览器原生弹窗。零新增第三方依赖（用标准库 `hmac`/`base64` 自签 token）。

### 3.1 会话 token

- Cookie 名: `console_session`
- 值: `base64url(payload).signature`
  - `payload` = JSON `{"exp": <unix 过期秒>}`（base64url 编码）
  - `signature` = `hmac_sha256(secret, payload_b64)` 的 base64url
  - `secret` = `LOGS_PASSWORD`（密码变更即令所有旧会话失效，符合预期）
- Cookie 属性: `HttpOnly`、`SameSite=Lax`、`Path=/`、`Secure`（当请求为 https 时）。
- 有效期:
  - 勾选「记住我」→ `Max-Age = 30 天`，payload `exp` 同步为 30 天后。
  - 不勾选 → 不设 `Max-Age`（会话级 Cookie），payload `exp` 设为较短（如 12 小时）。
- 校验: 重算签名（`hmac.compare_digest`）+ 检查 `exp` 未过期。

> token 不携带用户名，仅作为「已通过校验」的凭证。

## 4. 后端改动

### 4.1 新增 / 修改文件

**`app/observability/auth.py`** （或新建 `app/console_auth.py`，实现时择一，倾向放在同处复用）
- `make_session_token(password, ttl_seconds) -> str`
- `verify_session_token(token, password) -> bool`
- `is_console_authed(request, username, password) -> bool`
  - 依次尝试: 合法 `console_session` Cookie → 现有 `Authorization` 头（Basic/Bearer，向后兼容）。
- 修改 `require_logs_auth(expected_password)`:
  - 在原有 `Authorization` 头校验之外，**额外接受合法的 `console_session` Cookie**。
  - 失败仍返回 `401 JSON`（用于数据 API，不触发原生弹窗）。

**`app/config.py`**
- 新增 `logs_user: str = os.getenv("LOGS_USER", "admin")`。

**`main.py`**
- `GET /login`（HTMLResponse，**不鉴权**）→ 返回 `web/login.html`。
- `POST /api/console/login`:
  - body: `{"username": str, "password": str, "remember": bool}`
  - 校验: `hmac.compare_digest(username, LOGS_USER)` **且** `hmac.compare_digest(password, LOGS_PASSWORD)`。
  - 成功: `Set-Cookie: console_session=...`，返回 `200 {"ok": true}`。
  - 失败: 返回 `401 {"ok": false, "error": "用户名或密码错误"}`（不区分是用户名错还是密码错）。
  - 未配置 `LOGS_PASSWORD`: 返回 `503`（与现状一致）。
- `POST /api/console/logout`: 清除 `console_session` Cookie，返回 `200`。
- **页面路由改造** `/`、`/config`、`/evaluations`、`/logs`:
  - 不再用会抛 401 的依赖；改为在处理函数内调用 `is_console_authed(...)`。
  - 未登录 → 返回 `RedirectResponse(f"/login?next=<当前路径>", status_code=302)`，避免触发浏览器原生弹窗。
  - 已登录 → 正常返回页面。

### 4.2 兼容性

- observability 数据 API（`build_obs_router(... auth_dep=require_logs_auth(...))`）与 `logs.html` 的
  `credentials:'include'` fetch：登录后浏览器自动带 `console_session` Cookie → `require_logs_auth` 接受 → 继续可用。
- 程序化访问仍可用 `Authorization: Bearer/Basic`（向后兼容，不破坏现有脚本/测试）。

## 5. 前端：`web/login.html`

风格 A（浅色，贴合现有控制台设计 tokens）。独立单页，自带样式，不依赖现有大页面。

### 5.1 视觉与元素

- 居中白卡片；背景沿用 `radial-gradient(...) + #f7f8fa`；主色 `#2f80ed`，渐变按钮 `linear-gradient(135deg,#2f80ed,#1d4ed8)`；字体 `Inter, "Noto Sans JP"`。
- 顶部: 渐变方形 logo（锁形 icon）+ 标题 `Mercari コンソール` + 副标题 `商品画像識別テスター`。
- 字段:
  1. **用户名**（user icon，文本框）
  2. **密码**（lock icon，密码框，右侧眼睛图标切换显示/隐藏）
- **记住我** 复选框（默认勾选，文案「记住我（30 天免登录）」）。
- 主按钮「登 录」。
- 右下角 **中日语言切换**（跟随并写入现有语言偏好键，与其它页面一致）。

### 5.2 三种状态（已可视化确认）

- **默认态**: 空表单。
- **加载态**: 提交时按钮变为 spinner +「登录中…」，禁用重复提交。
- **错误态**: 两个输入框红色描边 + 轻微抖动动画 + 卡片内联红色提示条「用户名或密码错误，请重试」。**全程无原生 `alert`/`prompt`。**

### 5.3 交互逻辑

- 读取 URL `?next=` 参数作为登录成功后的跳转目标（默认 `/`）；对 `next` 做白名单/同源校验，避免开放重定向。
- 回车提交；提交期间禁用按钮。
- `fetch('/api/console/login', {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', body: JSON.stringify({username, password, remember})})`
  - `200` → `window.location.replace(next)`。
  - `401` → 进入错误态，聚焦用户名框。
  - `503` → 提示「登录未配置（请设置 LOGS_PASSWORD）」。
- i18n: 中/日两套文案，结构与现有页面的语言表一致；初始语言读现有 localStorage 偏好键。

### 5.4 退出登录入口

- 在 `web/index.html` 顶部增加轻量「退出登录」入口。
- 点击 → `fetch('/api/console/logout', {method:'POST', credentials:'same-origin'})` → `window.location.replace('/login')`。

## 6. 测试（pytest，沿用现有风格）

新增/调整测试（参考现有 `tests/test_observability_auth.py`、`tests/test_console_routes.py`）:

1. `POST /api/console/login` 正确用户名+密码 → 200 且 `Set-Cookie: console_session`。
2. 错误密码 / 错误用户名 → 401，错误信息统一，无 `Set-Cookie`。
3. 未设 `LOGS_PASSWORD` → 503。
4. 带合法 `console_session` Cookie 访问受保护数据 API → 200。
5. 篡改/过期 token → 视为未授权。
6. 未登录访问页面路由（`/`、`/config`、`/evaluations`、`/logs`）→ 302 重定向到 `/login?next=...`。
7. `POST /api/console/logout` → 清除 Cookie。
8. 向后兼容: `Authorization: Bearer <LOGS_PASSWORD>` 与 Basic 仍可访问数据 API。
9. `remember=true/false` 对应 Cookie 是否带 `Max-Age`。
10. `next` 开放重定向防护: 外部 URL 被拒/回退到 `/`。

## 7. 配置与文档

- `.env.example` 增加 `LOGS_USER`（默认 admin）说明，并补充 `LOGS_PASSWORD` 现已用于登录页的说明。
- `README.md` / `API.md` 更新登录说明（如有访问指引章节）。

## 8. 实现顺序（概览）

1. 后端: config 增 `LOGS_USER` → auth 模块加 token/cookie 逻辑 → login/logout 路由 → 页面路由改重定向。
2. 前端: `web/login.html`（风格 A）→ index.html 加退出入口。
3. 测试: 补齐第 6 节用例。
4. 文档: `.env.example` / README。
