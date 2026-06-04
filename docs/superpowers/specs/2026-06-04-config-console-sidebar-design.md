# 配置控制台 UI 升级（共享侧边栏）— 设计文档

日期：2026-06-04

## 背景

当前 `web/` 下有三个互相独立、无导航联系的静态页：

- `index.html` —— 前端测试页（图片识别 / 标题分类 / 图片合成 三个工具，外加共享的"识别结果 / 统计 / 接口地址"卡片）。由 FastAPI `@app.get("/")` 服务，**当前公开无鉴权**。
- `config.html` —— API 配置 + 提示词配置（顶部 tab 切换）。由 `@app.get("/config")` 服务，受 `LOGS_PASSWORD` 的 HTTP Basic 认证保护。
- `logs.html` —— 日志查看器（GitHub 风格，独立 UI）。由 `@app.get("/logs")` 服务，同样受 Basic 认证保护。

参考项目 `ai_customer_service/app/static` 用一个共享的 `shell.js` 在每个页面构建"顶栏 + 左侧边栏"，一级入口用 `href` 跳转、页内用 `hash` 路由切 lvl2/lvl3。

## 目标

把配置页和测试页升级成带**统一顶栏 + 左侧边栏**的控制台，参考 `shell.js` 的组织方式，把不同功能放进侧边栏作为入口：

1. 前端测试页折入控制台，并随之进入受 `LOGS_PASSWORD` 保护的区域（需要密码才能访问）。
2. 日志页保持完全独立、内部 UI 与功能不变，**不进**侧边栏。

## 已确认的决策

- **密码机制**：复用后端已有的 HTTP Basic 认证（`require_logs_auth(settings.logs_password)`）。
- **架构**：各页独立 HTML + 共享侧边栏 chrome（参考项目做法），非单页合并。
- **日志页**：完全独立，不进侧边栏，本次不改。

## 架构

### 共享静态资源（新增）

- **`web/assets/shell.js`** —— 精简版 shell。构建顶栏 + 左侧边栏；处理一级入口跳转（`href`）与页内 `hash` 路由（lvl2 高亮 + `onRouteChange` 回调）。从参考 `shell.js` 裁掉 lvl3 与 dirty 跟踪等本期不需要的部分，保留：
  - `Shell.mount({ page, defaultRoute, sidebar, onRouteChange, headerInfo? })`
  - `Shell.setHeader({ title, crumb?, actions? })`
  - `Shell.navigate(hash)`
  - `Shell.refreshSidebar()`
  - 沿用参考做法：mount 时把页面已有内容搬进 shell 主区（`#shellPageContent`），并保留 body 级浮层（如 lightbox/toast）不被搬动。
- **`web/assets/shell.css`** —— 只负责 chrome（顶栏 `.shell-topbar`、侧边栏 `.shell-sidebar`、主区 `.shell-body/.shell-main/.page-content/.page-header`）。复用现有设计 token（`--accent #2f80ed`、圆角卡片、阴影等），与现有视觉风格一致。每个页面**保留自己原有的组件样式**（表单、卡片、结果区、prompt 编辑器），把改动面降到最低。

### 侧边栏结构（lvl1 → lvl2）

```
顶栏:  [Mercari 识别]  ……………………  [↻ 刷新]
──────────────────────────────────
▾ 配置          (href → /config)
    · API 配置        #api
    · 提示词配置      #prompts
▸ 测试          (href → /)
    · 图片识别        #image
    · 标题分类        #title
    · 图片合成        #showcase
```

一级入口 PAGES：`[{ id:'config', label:'配置', href:'/config' }, { id:'test', label:'测试', href:'/' }]`。日志不在侧边栏。

## 页面改造

### config.html

- 去掉顶部 `.tabs` 两个按钮；改由 `Shell.mount` 注入 chrome。
- `sidebar()` 返回 `[{id:'api', label:'API 配置'}, {id:'prompts', label:'提示词配置'}]`。
- `onRouteChange('#api'|'#prompts')` 切换现有 `#tab-api` / `#tab-prompts` 两个 panel（沿用现有 `hidden` 显隐逻辑，仅把触发源从 tab 按钮换成侧边栏 + hash）。
- 顶部 `<h1>API 配置</h1>` 与状态行通过 `Shell.setHeader()` 设置（状态点/文案可放进 header 的 crumb 或 actions 区，保持现有读取-配置反馈）。
- 默认路由 `#api`。

### 测试页（现 index.html）

- 去掉顶部 3 个 `.tab-btn`；改由 shell 注入 chrome。
- `sidebar()` 返回 `[{id:'image', label:'图片识别'}, {id:'title', label:'标题分类'}, {id:'showcase', label:'图片合成'}]`。
- `onRouteChange('#image'|'#title'|'#showcase')` 调用现有的 `setActiveTab('image'|'title'|'showcase')`（该函数已存在，几乎零改动，只需把入参由按钮点击改为 hash 驱动）。
- 共享的"识别结果 / 统计 / 接口地址"卡片继续留在主区下方（跨工具共享，不随 lvl2 切换隐藏，与当前行为一致）。
- `<h1 id="page-title">` 通过 `Shell.setHeader()` 设置。
- 默认路由 `#image`。

## 后端改造（main.py）

1. **给测试页加锁**：`@app.get("/")` 增加 `dependencies=[Depends(require_logs_auth(settings.logs_password))]`，与 `/config` 同一把锁。测试页**仍留在 `/`**（保持"页面与 API 同源、请求里 endpoint 可留空"的现有设计，见 `index_page` 注释），只是现在需要密码。
2. **服务共享资源**：新增静态挂载让 FastAPI 能服务 `web/assets/`，例如 `app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")))`，页面以 `/assets/shell.js`、`/assets/shell.css` 引用。`shell.js/.css` 不含敏感信息，不加锁。

## 数据流

- 进入 `/config` 或 `/` → 浏览器原生 Basic 弹窗输入密码 → FastAPI 校验 `LOGS_PASSWORD` 通过 → 返回 HTML。
- 页面加载 `/assets/shell.js` + `/assets/shell.css` → `Shell.mount(...)` 构建 chrome、把原有内容搬进主区、按当前 hash 触发首个 `onRouteChange`。
- 侧边栏一级入口点击 → 普通 `href` 跳转（跨页，触发新页的 Basic 已在同一会话缓存，不再弹窗）。
- 侧边栏 lvl2 点击 → 改 `location.hash` → `hashchange` → `onRouteChange` → 页面切 panel/工具。
- 配置/提示词的保存仍走现有受保护的 `PUT /api/v1/config`、`PUT /api/v1/prompts`、`POST /api/v1/prompts/reset`（不变）。

## 错误处理

- 未配置 `LOGS_PASSWORD` 时，后端 `require_logs_auth` 返回 503（现有行为），`/` 与 `/config` 都不可用——与现状一致，仅多覆盖了 `/`。
- 密码错误 → 401 + Basic 重新弹窗（现有行为）。
- `shell.js/.css` 404（如挂载漏配）→ 页面退化为无 chrome 的裸内容；为此页面仍保留各自的基本可用性（内容本身不依赖 shell 才能渲染数据，shell 仅提供导航壳）。

## 已知边界

- Basic 认证只在 **FastAPI 直接服务页面**时生效。`web/netlify.toml` 的纯静态部署不会拦截测试页或注入鉴权——本设计以 FastAPI 部署为准，控制台在 Netlify 纯静态托管下的鉴权不在本期范围。
- 本期不引入 dirty/未保存改动拦截（参考项目里的 `beforeLeave` 守卫）；配置与提示词均有显式保存按钮，暂不需要。如后续需要可再加。

## 不做的事（YAGNI）

- 不合并成单页 SPA。
- 不改日志页 `logs.html` 的 UI 或功能。
- 不重写各页现有的组件样式，只新增 chrome 样式。
- 不加 lvl3 侧边栏、不加 env/model 顶栏信息（除非现有状态行迁移需要）。

## 测试与验收

- `/` 与 `/config` 未带密码时返回 401；带正确密码时返回对应 HTML。
- `/assets/shell.js`、`/assets/shell.css` 可被 FastAPI 正常服务（200）。
- 配置页：侧边栏「API 配置 / 提示词配置」可切换，对应 panel 正确显隐；保存/重读/提示词保存与恢复仍工作。
- 测试页：侧边栏「图片识别 / 标题分类 / 图片合成」可切换，对应工具卡片正确显隐；上传识别、标题分类、图片合成功能仍工作；共享结果/统计区正常。
- 一级入口「配置 ↔ 测试」可互相跳转，同会话内不重复弹密码框。
- 日志页 `/logs` 不受影响。
- 浏览器实测（Playwright）：登录、两页侧边栏切换、核心功能各跑一次。
