# 控制台账号管理与菜单权限设计

- 日期: 2026-06-19
- 状态: 已确认设计，待实现
- 目标: 在现有控制台登录基础上增加简单账号管理和菜单级权限。`.env` 中的账号是唯一超级管理员，超级管理员可以创建子账号，并给子账号分配可访问的一级菜单。

## 1. 背景与现状

当前控制台已使用自定义登录页和 `console_session` Cookie：

- 超级管理员凭据来自 `.env`：`LOGS_USER` / `LOGS_PASSWORD`。
- 控制台页面包括 `/`、`/config`、`/evaluations`、`/logs`。
- 共享菜单由 `web/assets/shell.js` 渲染，目前一级菜单固定为「配置」「测试」「模型测试」。
- 部分控制台 API 已挂 `require_logs_auth(settings.logs_password)`，但模型测试等 API 主要依赖页面登录入口，没有完整菜单级服务端校验。

本次新增的是轻量后台账号能力，不引入复杂角色、组织、审计或外部身份系统。

## 2. 已确认决策

| 项 | 决定 |
|----|------|
| 超级管理员 | 仍为 `.env` 中的 `LOGS_USER` / `LOGS_PASSWORD` |
| 子账号存储 | `data/console_users.json` |
| 密码存储 | 子账号密码哈希保存，不保存明文 |
| 权限粒度 | 只做到一级菜单级别 |
| 账号管理权限 | 只允许超级管理员访问，子账号不能访问或被授予 |
| 菜单访问控制 | 前端隐藏 + 服务端页面/API 校验 |

## 3. 菜单权限模型

菜单 ID 固定为以下集合：

| 菜单 ID | 菜单名称 | 页面 | 主要 API 范围 |
|----|----|----|----|
| `test` | 测试 | `/` | 当前测试页调用的控制台相关接口 |
| `config` | 配置 | `/config` | `/api/v1/config`、`/api/v1/prompts` |
| `evaluations` | 模型测试 | `/evaluations` | `/api/v1/evaluations...`、`/api/v1/image-proxy` |
| `logs` | 日志 | `/logs` | observability router 暴露的日志接口 |
| `accounts` | 账号管理 | `/accounts` | `/api/v1/console/users...` |

规则：

- 超级管理员固定拥有全部菜单，包含 `accounts`。
- 子账号只能拥有 `test`、`config`、`evaluations`、`logs` 中的一个或多个菜单。
- 子账号永远不能拥有 `accounts`，即使 JSON 文件被手工写入也要在服务端过滤。
- 未授权访问页面时返回 403 页面或重定向到该账号的第一个可访问菜单；数据 API 返回 403 JSON。
- 未登录仍保持现有行为：页面路由跳转 `/login?next=...`，数据 API 返回 401/403。

## 4. 数据文件

新增 `data/console_users.json`。文件由服务端自动创建，结构如下：

```json
{
  "version": 1,
  "users": [
    {
      "username": "model-tester",
      "password_hash": "pbkdf2_sha256$260000$base64salt$base64hash",
      "menus": ["evaluations"],
      "enabled": true,
      "created_at": "2026-06-19T10:00:00Z",
      "updated_at": "2026-06-19T10:00:00Z"
    }
  ]
}
```

约束：

- `username` 去首尾空格后必须非空，大小写敏感；不能等于超级管理员用户名。
- `password_hash` 使用标准库 `hashlib.pbkdf2_hmac("sha256", ...)`，随机 salt，常量时间校验。
- `menus` 只保存允许给子账号的菜单 ID，写入前去重、排序并过滤非法值。
- `enabled=false` 的账号不能登录。
- 写文件采用临时文件 + 原子替换，避免部分写入导致 JSON 损坏。
- JSON 损坏时账号管理 API 返回 500，登录子账号失败；超级管理员仍可用 `.env` 登录，方便人工修复文件。

## 5. 后端设计

### 5.1 新增账号服务模块

新增 `app/console_accounts.py`：

- `ConsoleAccountStore(path: Path)`：读写 `data/console_users.json`。
- `hash_password(password: str) -> str`。
- `verify_password(password: str, encoded_hash: str) -> bool`。
- `authenticate_subaccount(username, password) -> ConsoleUser | None`。
- `list_users()`：返回不含密码哈希的用户列表。
- `create_user(username, password, menus, enabled=True)`。
- `update_user(username, password=None, menus=None, enabled=None)`。
- `delete_user(username)`。

### 5.2 会话 token

修改 `app/observability/auth.py` 的 token payload，从只包含过期时间扩展为：

```json
{
  "exp": 1781863200,
  "sub": "model-tester",
  "role": "subaccount",
  "menus": ["evaluations"]
}
```

超级管理员 payload：

```json
{
  "exp": 1781863200,
  "sub": "admin",
  "role": "superadmin",
  "menus": ["test", "config", "evaluations", "logs", "accounts"]
}
```

兼容性：

- 旧 token 只有 `exp` 时仍可视为已登录超级管理员，避免发布后已有管理员会话立即失效。
- `Authorization: Basic/Bearer <LOGS_PASSWORD>` 继续视为超级管理员，用于现有测试和脚本兼容。
- 签名 secret 仍绑定 `LOGS_PASSWORD`，超级管理员密码变更会使所有旧会话失效。

### 5.3 登录流程

`POST /api/v1/console/login`：

1. 先校验 `.env` 超级管理员用户名和密码。
2. 超级管理员匹配成功，签发 `role=superadmin`、全菜单 token。
3. 超级管理员不匹配时，读取 `data/console_users.json` 校验子账号。
4. 子账号匹配成功，签发 `role=subaccount`、该账号菜单 token。
5. 全部失败返回现有 401 文案，不暴露具体原因。

### 5.4 当前用户接口

新增 `GET /api/v1/console/me`：

```json
{
  "username": "model-tester",
  "role": "subaccount",
  "menus": ["evaluations"],
  "defaultPath": "/evaluations"
}
```

用途：

- `shell.js` 获取当前用户可见菜单。
- 登录页已登录时可以跳转到该用户默认页面。
- 前端页面可用它决定空状态或无权限跳转。

### 5.5 权限依赖

新增后端辅助函数：

- `get_console_identity(request) -> ConsoleIdentity | None`。
- `require_console_menu(menu_id)`：数据 API 使用，未登录返回 401，已登录但无菜单返回 403。
- `ensure_page_menu(request, menu_id)`：页面路由使用，未登录跳登录页，无权限返回 403 页面或跳默认页。
- `require_superadmin`：账号管理 API 使用。

页面映射：

- `/` 需要 `test`。
- `/config` 需要 `config`。
- `/evaluations` 需要 `evaluations`。
- `/logs` 需要 `logs`。
- `/accounts` 需要 `accounts` 且必须是超级管理员。

API 映射：

- `PUT /api/v1/config`、`PUT /api/v1/prompts`、`POST /api/v1/prompts/reset` 需要 `config`。
- `/api/v1/evaluations...` 全部需要 `evaluations`。
- `/api/v1/image-proxy` 需要 `evaluations`。
- observability router 需要 `logs`。
- `/api/v1/console/users...` 需要超级管理员。
- `GET /api/v1/config` 和 `GET /api/v1/prompts` 若只服务配置页，也需要 `config`。

## 6. 前端设计

### 6.1 共享菜单

修改 `web/assets/shell.js`：

- 启动时请求 `/api/v1/console/me`。
- 只渲染 `me.menus` 中允许的一级菜单。
- 顶栏显示当前用户名和「退出」。
- 如果当前页面不在允许菜单中，跳转 `me.defaultPath` 或展示无权限状态。

一级菜单增加「账号管理」：

```js
{ id: "accounts", label: "账号管理", href: "/accounts" }
```

### 6.2 账号管理页面

新增 `web/accounts.html`：

- 只给超级管理员访问。
- 左侧共享 shell 一级菜单中显示「账号管理」。
- 主区为账号列表和编辑表单。

功能：

- 查看子账号列表：用户名、启用状态、菜单权限、更新时间。
- 新建子账号：用户名、密码、菜单权限复选框、启用状态。
- 编辑子账号：重置密码、修改菜单权限、启用/停用。
- 删除子账号：二次确认。

UI 约束：

- 权限选择只显示 `test`、`config`、`evaluations`、`logs`。
- 不展示 `accounts` 作为可分配权限。
- 表单错误以内联消息展示，不使用原生 `alert`。

## 7. 错误处理

- 子账号用户名重复：400。
- 子账号用户名等于超级管理员用户名：400。
- 密码为空或过短：400，建议最少 6 位。
- 菜单为空：400，子账号至少拥有一个菜单。
- 非法菜单 ID：写入前过滤；过滤后为空则 400。
- 非超级管理员访问账号管理页面或 API：403。
- 子账号被停用后，已有 token 在下一次请求时要重新校验账号状态并拒绝访问。

## 8. 测试计划

后端 pytest：

1. 超级管理员登录仍成功，`/api/v1/console/me` 返回全菜单和 `role=superadmin`。
2. 创建子账号写入 `data/console_users.json`，返回列表不包含密码哈希。
3. 子账号可用正确密码登录，错误密码不能登录。
4. 子账号只带 `evaluations` 权限时，可访问 `/evaluations` 和 `/api/v1/evaluations`，不能访问 `/config`、`/accounts`。
5. 子账号不能被授予 `accounts`，手工写入也会被过滤。
6. 停用子账号后，登录失败，已有 token 请求返回 403。
7. Basic/Bearer 密码认证仍作为超级管理员兼容。
8. JSON 文件不存在时自动初始化为空用户集。
9. JSON 文件损坏时子账号登录失败但超级管理员登录仍成功。

前端轻量测试：

- `shell.js` 根据 `/api/v1/console/me` 返回菜单过滤一级导航。
- 账号管理页面创建、编辑、删除请求路径和错误展示正确。

## 9. 不做的事

- 不做按钮级、字段级、数据行级权限。
- 不做多个管理员或管理员角色继承。
- 不做登录审计、密码找回、注册邀请、验证码。
- 不把子账号写入 `.env`。
- 不引入数据库或第三方认证依赖。

## 10. 实现顺序

1. 后端账号服务与密码哈希。
2. 会话 token identity 扩展与 `/api/v1/console/me`。
3. 登录流程支持超级管理员和子账号。
4. 页面与 API 权限依赖。
5. `shell.js` 菜单过滤。
6. `accounts.html` 与账号 CRUD API。
7. 测试与文档更新。
