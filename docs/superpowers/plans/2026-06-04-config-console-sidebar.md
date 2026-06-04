# 配置控制台侧边栏 UI 升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给配置页和测试页加上统一的"顶栏 + 左侧边栏"控制台外壳（参考 `ai_customer_service/shell.js`），把不同功能作为侧边栏入口；测试页随之进入受 `LOGS_PASSWORD` 保护的区域；日志页不动。

**Architecture:** 各页保持独立 HTML，新增共享静态资源 `web/assets/shell.js` + `web/assets/shell.css` 提供 chrome 与 hash 路由。后端 `main.py` 增加 `/assets` 静态挂载，并给 `/`（测试页）加上与 `/config` 相同的 Basic 认证依赖。配置页/测试页用 hash 路由切换原有的 tab panel，触发源从顶部按钮换成侧边栏。

**Tech Stack:** FastAPI（StaticFiles、Depends）、原生 JS（无构建步骤的浏览器脚本）、pytest + TestClient（后端测试）、Playwright MCP（前端端到端验证）。

设计依据：`docs/superpowers/specs/2026-06-04-config-console-sidebar-design.md`

---

## 文件结构

- **Create** `web/assets/shell.css` —— 仅 chrome 样式（顶栏、侧边栏、主区布局）。
- **Create** `web/assets/shell.js` —— 共享 shell 模块（`window.Shell`）：顶栏 + 侧边栏 + hash 路由。
- **Create** `web/assets/.gitkeep` —— 占位，保证目录在挂载前存在（实际有 shell.* 文件后可省，但先建目录避免 StaticFiles 启动报错）。
- **Modify** `main.py` —— 新增 `/assets` 挂载；给 `@app.get("/")` 加 Basic 认证依赖。
- **Create** `tests/test_console_routes.py` —— 后端路由测试（`/` 鉴权、`/assets/*` 可服务）。
- **Modify** `web/config.html` —— 去顶部 tabs，接入 shell，hash 切 `#api/#prompts`。
- **Modify** `web/index.html` —— 去顶部 tabs，接入 shell，hash 切 `#image/#title/#showcase`。

---

## Task 1: 共享 chrome 样式 `web/assets/shell.css`

**Files:**
- Create: `web/assets/shell.css`
- Create: `web/assets/.gitkeep`

- [ ] **Step 1: 建目录占位**

Run:
```bash
mkdir -p web/assets && touch web/assets/.gitkeep
```

- [ ] **Step 2: 写 shell.css**

Create `web/assets/shell.css`:

```css
/* Shared console chrome: topbar + left sidebar + main layout.
   Component styles (forms, cards, results) stay in each page. */
:root {
  --shell-accent: #2f80ed;
  --shell-border: #e5e7eb;
  --shell-muted: #6b7280;
  --shell-text: #1c1f25;
  --shell-bg: #f7f8fa;
  --shell-sidebar-w: 232px;
  --shell-topbar-h: 56px;
}

body.shell-page {
  margin: 0;
  background: var(--shell-bg);
  color: var(--shell-text);
  font-family: "Inter", "Noto Sans SC", "Noto Sans JP", system-ui, -apple-system, sans-serif;
}

.shell-topbar {
  position: fixed;
  top: 0; left: 0; right: 0;
  height: var(--shell-topbar-h);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  background: #fff;
  border-bottom: 1px solid var(--shell-border);
  z-index: 50;
}
.shell-topbar .brand-logo .logo {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border-radius: 8px;
  background: linear-gradient(135deg, var(--shell-accent), #1d4ed8);
  color: #fff; font-weight: 800; font-size: 13px;
}
.shell-topbar .brand-text { font-weight: 800; letter-spacing: -0.2px; }
.shell-topbar .spacer { flex: 1; }
.shell-topbar .tb-refresh {
  width: 34px; height: 34px; border-radius: 8px;
  border: 1px solid var(--shell-border); background: #fff;
  color: var(--shell-muted); cursor: pointer; font-size: 16px;
}
.shell-topbar .tb-refresh:hover { color: var(--shell-accent); border-color: var(--shell-accent); }

.shell-body {
  display: flex;
  padding-top: var(--shell-topbar-h);
  min-height: 100vh;
}

.shell-sidebar {
  position: fixed;
  top: var(--shell-topbar-h); bottom: 0; left: 0;
  width: var(--shell-sidebar-w);
  overflow-y: auto;
  background: #fff;
  border-right: 1px solid var(--shell-border);
  padding: 14px 10px;
}
.shell-sidebar .nav-group {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 12px; border-radius: 10px;
  color: var(--shell-text); text-decoration: none;
  font-weight: 700; font-size: 14px;
}
.shell-sidebar .nav-group .ic { color: var(--shell-muted); font-size: 11px; width: 12px; }
.shell-sidebar .nav-group.on { background: #eef2ff; color: #1e3a8a; }
.shell-sidebar .sub-list { display: grid; gap: 2px; margin: 2px 0 8px 14px; }
.shell-sidebar .sub-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px; border-radius: 8px;
  color: var(--shell-muted); text-decoration: none; font-size: 13px;
}
.shell-sidebar .sub-item:hover { background: #f5f7fb; color: var(--shell-text); }
.shell-sidebar .sub-item.on { background: var(--shell-accent); color: #fff; font-weight: 700; }

.shell-main {
  margin-left: var(--shell-sidebar-w);
  flex: 1;
  min-width: 0;
  padding: 22px 24px 40px;
}
.shell-main .page-header {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 18px;
}
.shell-main .page-header h1 { margin: 0; font-size: 24px; letter-spacing: -0.2px; }
.shell-main .page-header .crumb { color: var(--shell-muted); font-size: 13px; margin-top: 4px; }
.shell-main .page-header .actions { display: flex; align-items: center; gap: 10px; }
.shell-main .page-content { max-width: 1180px; }

@media (max-width: 720px) {
  .shell-sidebar { position: static; width: 100%; height: auto; border-right: none; border-bottom: 1px solid var(--shell-border); }
  .shell-body { display: block; }
  .shell-main { margin-left: 0; }
}
```

- [ ] **Step 3: Commit**

```bash
git add web/assets/shell.css web/assets/.gitkeep
git commit -m "feat(web): add shared console chrome stylesheet"
```

---

## Task 2: 共享 shell 模块 `web/assets/shell.js`

**Files:**
- Create: `web/assets/shell.js`

裁剪自参考 `shell.js`：去掉 lvl3、dirty 跟踪、beforeunload 守卫、env/model 信息槽，保留顶栏 + 侧边栏 + hash 路由 + 内容搬移。

- [ ] **Step 1: 写 shell.js**

Create `web/assets/shell.js`:

```js
/* Shared console chrome for the config + test pages.
 *
 * Usage:
 *   Shell.mount({
 *     page: 'config',                 // 'config' | 'test'
 *     defaultRoute: 'api',
 *     brand: { logo: 'M', text: 'Mercari 识别' },   // optional
 *     sidebar: () => [{ id, label }, ...],          // lvl2 items for active page
 *     onRouteChange: (route) => {...},              // called on hashchange + initial
 *   });
 *   Shell.setHeader({ title, crumb, actions });     // actions: Node | Node[]
 *   Shell.navigate(hash);
 *   Shell.refreshSidebar();
 */
(function (global) {
  const PAGES = [
    { id: 'config', label: '配置', href: '/config' },
    { id: 'test',   label: '测试', href: '/' },
  ];

  // Body-level overlays that must NOT be moved into the shell main area.
  const OVERLAY_IDS = new Set(['lightbox', 'toast', 'modalBackdrop']);

  let config = null;

  function el(tag, props = {}, children = []) {
    const n = document.createElement(tag);
    for (const k of Object.keys(props)) {
      if (k === 'class') n.className = props[k];
      else if (k === 'text') n.textContent = props[k];
      else if (k === 'html') n.innerHTML = props[k];
      else if (k.startsWith('on') && typeof props[k] === 'function')
        n.addEventListener(k.slice(2).toLowerCase(), props[k]);
      else if (props[k] === false || props[k] == null) continue;
      else n.setAttribute(k, props[k]);
    }
    for (const c of children) if (c) n.append(c);
    return n;
  }

  function mount(cfg) {
    config = cfg;
    document.body.classList.add('shell-page');

    // Move existing page content (everything that's not a script/style/overlay)
    // into the shell main content area.
    const preserved = [];
    Array.from(document.body.children).forEach((n) => {
      if (n.tagName === 'SCRIPT' || n.tagName === 'STYLE') return;
      if (OVERLAY_IDS.has(n.id)) return;
      preserved.push(n);
      n.remove();
    });

    const brand = cfg.brand || { logo: 'M', text: 'Mercari 识别' };
    const topbar = el('header', { class: 'shell-topbar' }, [
      el('span', { class: 'brand-logo', html: `<span class="logo" aria-hidden="true">${escapeHtml(brand.logo)}</span>` }),
      el('span', { class: 'brand-text', text: brand.text }),
      el('span', { class: 'spacer' }),
      el('button', { class: 'tb-refresh', type: 'button', title: '刷新',
        onClick: () => window.location.reload(), text: '↻' }),
    ]);

    const sidebar = el('aside', { id: 'shellSidebar', class: 'shell-sidebar' });
    const pageContent = el('div', { id: 'shellPageContent', class: 'page-content' });
    preserved.forEach((n) => pageContent.appendChild(n));
    const main = el('main', { id: 'shellMain', class: 'shell-main' }, [
      el('div', { id: 'shellPageHeader', class: 'page-header' }, [
        el('div', {}, [
          el('h1', { id: 'shellPageTitle', text: '' }),
          el('div', { id: 'shellPageCrumb', class: 'crumb', text: '' }),
        ]),
        el('div', { id: 'shellPageActions', class: 'actions' }),
      ]),
      pageContent,
    ]);
    const body = el('div', { class: 'shell-body' }, [sidebar, main]);

    document.body.prepend(topbar);
    document.body.append(body);

    renderSidebar();
    window.addEventListener('hashchange', onHashChange);
    if (!window.location.hash && config.defaultRoute) {
      history.replaceState(null, '', '#' + config.defaultRoute);
    }
    onHashChange();  // initial dispatch
  }

  function currentRoute() {
    return (window.location.hash || '#' + (config.defaultRoute || '')).slice(1);
  }

  function renderSidebar() {
    const root = document.getElementById('shellSidebar');
    if (!root) return;
    root.innerHTML = '';
    const lvl2 = config.sidebar ? config.sidebar() : [];
    const route = currentRoute();

    for (const p of PAGES) {
      const isCurrent = p.id === config.page;
      root.append(el('a', {
        class: 'nav-group' + (isCurrent ? ' on' : ''),
        href: p.href,
      }, [
        el('span', { class: 'ic', text: isCurrent ? '▾' : '▸' }),
        el('span', { text: p.label }),
      ]));

      if (isCurrent && lvl2.length) {
        const subList = el('div', { class: 'sub-list' });
        for (const item of lvl2) {
          subList.append(el('a', {
            class: 'sub-item' + (item.id === route ? ' on' : ''),
            href: '#' + item.id,
          }, [ el('span', { text: item.label }) ]));
        }
        root.append(subList);
      }
    }
  }

  function onHashChange() {
    if (config.onRouteChange) config.onRouteChange(currentRoute());
    renderSidebar();
  }

  function setHeader({ title, crumb, actions } = {}) {
    const t = document.getElementById('shellPageTitle');
    const c = document.getElementById('shellPageCrumb');
    const a = document.getElementById('shellPageActions');
    if (t) t.textContent = title || '';
    if (c) c.textContent = crumb || '';
    if (a) {
      a.innerHTML = '';
      if (Array.isArray(actions)) actions.forEach((x) => x && a.append(x));
      else if (actions instanceof Node) a.append(actions);
    }
  }

  function navigate(hash) {
    window.location.hash = hash.startsWith('#') ? hash.slice(1) : hash;
  }

  function refreshSidebar() { renderSidebar(); }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  global.Shell = { mount, setHeader, navigate, refreshSidebar };
})(window);
```

- [ ] **Step 2: Commit**

```bash
git add web/assets/shell.js
git commit -m "feat(web): add shared console shell module (topbar + sidebar + hash routing)"
```

---

## Task 3: 后端 —— 挂载 `/assets` 并给测试页 `/` 加密码锁

**Files:**
- Modify: `main.py`（`@app.get("/")` 约 544 行；`WEB_DIR` 约 541 行）
- Test: `tests/test_console_routes.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_console_routes.py`:

```python
import base64
import importlib

import app.config
import main


def _reload_with_password(monkeypatch, password: str):
    monkeypatch.setenv("LOGS_PASSWORD", password)
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def _auth(password: str) -> dict:
    creds = base64.b64encode(f"a:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def test_index_requires_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        assert client.get("/").status_code == 401


def test_index_served_with_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/", headers=_auth("secret"))
        assert r.status_code == 200
        assert "shell.js" in r.text  # test page now includes the shared shell


def test_shell_assets_served_without_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        assert client.get("/assets/shell.js").status_code == 200
        assert client.get("/assets/shell.css").status_code == 200
```

> 注：`test_index_served_with_password` 断言 `shell.js` 字符串，会在 Task 5（index.html 接入 shell）后才完全为真；本任务先让前两条和资源服务通过——若此条暂红，Task 5 完成后转绿。执行时可先用 `-k "requires_password or shell_assets"` 跑前置项。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_console_routes.py -k "requires_password or shell_assets" -v`
Expected: FAIL —— `/` 返回 200（未加锁）或 `/assets/*` 返回 404（未挂载）。

- [ ] **Step 3: 加静态挂载**

在 `main.py` 顶部 import 区（与其它 fastapi import 相邻）确认/新增：

```python
from fastapi.staticfiles import StaticFiles
```

在 `WEB_DIR = BASE_DIR / "web"`（约 541 行）之后、`@app.get("/")` 之前插入：

```python
ASSETS_DIR = WEB_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
```

- [ ] **Step 4: 给 `/` 加认证依赖**

把：

```python
@app.get("/", response_class=HTMLResponse)
def index_page():
```

改为：

```python
@app.get("/", response_class=HTMLResponse,
         dependencies=[Depends(require_logs_auth(settings.logs_password))])
def index_page():
```

（`Depends`、`require_logs_auth`、`settings` 均已在 main.py 顶部导入/定义，无需新增。）

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_console_routes.py -k "requires_password or shell_assets" -v`
Expected: PASS（3 条中除依赖 Task 5 的那条外全部通过）。

- [ ] **Step 6: 回归既有路由测试**

Run: `.venv/bin/python -m pytest tests/test_observability_auth.py tests/test_config_api.py -v`
Expected: PASS（未破坏既有鉴权与配置 API）。

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_console_routes.py
git commit -m "feat(api): mount /assets and gate test page / behind LOGS_PASSWORD"
```

---

## Task 4: 配置页接入 shell（侧边栏切 API/提示词）

**Files:**
- Modify: `web/config.html`（`<head>` 引样式；约 393-396 行 `<nav class="tabs">`；约 380-391 `<header>`；脚本里 tab 逻辑 669-683 行）

- [ ] **Step 1: 引入共享样式**

在 `web/config.html` 的 `</head>` 之前（约 378 行 `</style>` 之后）加：

```html
    <link rel="stylesheet" href="/assets/shell.css" />
```

- [ ] **Step 2: 删除页内顶部 header 与 tabs**

删除这段 `<header>…</header>`（约 382-391 行）：

```html
      <header>
        <div>
          <h1>API 配置</h1>
          <p class="status">
            <span class="dot" id="status-dot"></span>
            <span id="status-text">正在读取配置...</span>
          </p>
        </div>
        <span class="pill">同服务配置页</span>
      </header>
```

替换为（保留状态点供 JS 引用，但移出大标题——标题改由 shell 设置）：

```html
      <div id="config-status-host" hidden>
        <span class="dot" id="status-dot"></span>
        <span id="status-text">正在读取配置...</span>
      </div>
```

删除 `<nav class="tabs">…</nav>`（约 393-396 行）整段。

- [ ] **Step 3: 接入 shell —— 替换 tab 切换 JS**

把脚本里这段（约 669-683 行）：

```javascript
      // ---------- Tabs ----------
      const tabButtons = document.querySelectorAll(".tab-btn");
      const tabPanels = {
        api: document.getElementById("tab-api"),
        prompts: document.getElementById("tab-prompts"),
      };
      tabButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
          const tab = btn.getAttribute("data-tab");
          tabButtons.forEach((b) => b.classList.toggle("active", b === btn));
          Object.entries(tabPanels).forEach(([key, el]) => {
            el.hidden = key !== tab;
          });
        });
      });
```

替换为：

```javascript
      // ---------- Shell + sidebar routing ----------
      const tabPanels = {
        api: document.getElementById("tab-api"),
        prompts: document.getElementById("tab-prompts"),
      };
      function showPanel(route) {
        const key = tabPanels[route] ? route : "api";
        Object.entries(tabPanels).forEach(([k, el]) => { el.hidden = k !== key; });
        Shell.setHeader({
          title: key === "prompts" ? "提示词配置" : "API 配置",
          crumb: statusText ? statusText.textContent : "",
        });
      }
      Shell.mount({
        page: "config",
        defaultRoute: "api",
        brand: { logo: "M", text: "Mercari 识别" },
        sidebar: () => [
          { id: "api", label: "API 配置" },
          { id: "prompts", label: "提示词配置" },
        ],
        onRouteChange: showPanel,
      });
```

- [ ] **Step 4: 引入 shell.js（脚本顺序）**

在 `<body>` 末尾、现有内联 `<script>` **之前**，加：

```html
    <script src="/assets/shell.js"></script>
```

（必须先加载 `Shell` 再执行内联脚本的 `Shell.mount`。）

- [ ] **Step 5: 让状态文案刷新时同步到 header**

在内联脚本的 `setStatus` 函数体末尾追加一行，把最新状态写进面包屑（函数约 588-591 行）：

```javascript
      function setStatus(text, live) {
        statusText.textContent = text;
        statusDot.classList.toggle("live", Boolean(live));
        if (window.Shell && Shell.setHeader) {
          const active = document.getElementById("tab-prompts").hidden ? "API 配置" : "提示词配置";
          Shell.setHeader({ title: active, crumb: text });
        }
      }
```

- [ ] **Step 6: 浏览器验证（Playwright）**

Run（先确保本地服务起着，设置了 `LOGS_PASSWORD`）：
```bash
LOGS_PASSWORD=secret .venv/bin/python -m uvicorn main:app --port 8000 &
```
用 Playwright MCP 打开 `http://localhost:8000/config`（带 Basic 认证 `a:secret`）：
- Expected: 左侧出现「配置 / 测试」一级入口；配置下有「API 配置 / 提示词配置」；默认显示 API 配置面板，顶栏标题为「API 配置」。
- 点「提示词配置」→ URL 变 `#prompts`，面板切到提示词编辑器，标题变「提示词配置」。
- 点「API 配置」→ 切回，保存/重读按钮可见。

- [ ] **Step 7: Commit**

```bash
git add web/config.html
git commit -m "feat(web): wire config page into shell sidebar (api/prompts)"
```

---

## Task 5: 测试页接入 shell（侧边栏切 图片识别/标题分类/图片合成）

**Files:**
- Modify: `web/index.html`（`<head>`；顶部 `<h1>` 与 `.tab-btn` 约 1069-1092 行；`setActiveTab` 约 2408-2423 行；tab 按钮事件监听处）

- [ ] **Step 1: 引入共享样式**

在 `web/index.html` 的 `</style>` 之后（约 1063 行附近、`</head>` 之前）加：

```html
    <link rel="stylesheet" href="/assets/shell.css" />
```

- [ ] **Step 2: 删除页内 `<h1>` 与三个 tab 按钮**

删除大标题行（约 1069 行）：

```html
          <h1 id="page-title">Mercari 商品图片识别测试</h1>
```

删除三个 tab 按钮（约 1090-1092 行，连同其外层若是专门包裹 tab 的容器一并删；若该容器还含别的内容则只删这三行按钮）：

```html
            <button class="tab-btn active" id="tab-image">图片识别</button>
            <button class="tab-btn" id="tab-title">标题分类</button>
            <button class="tab-btn" id="tab-showcase">图片合成</button>
```

> 执行提示：先用 `grep -n 'tab-btn\|page-title' web/index.html` 定位精确行与外层标签，删按钮但保留同容器内的其它元素。

- [ ] **Step 3: 用 hash 驱动 setActiveTab**

`setActiveTab`（约 2408-2423 行）内部逻辑保留不变（它仍按 `activeTab` 切 `.hidden`/`.active`）。在其**函数体末尾**追加：同步顶栏标题：

```javascript
      function setActiveTab(tab) {
        if (tab === "title") {
          activeTab = "title";
        } else if (tab === "showcase") {
          activeTab = "showcase";
        } else {
          activeTab = "image";
        }
        tabImageBtn && tabImageBtn.classList.toggle("active", activeTab === "image");
        tabTitleBtn && tabTitleBtn.classList.toggle("active", activeTab === "title");
        tabShowcaseBtn && tabShowcaseBtn.classList.toggle("active", activeTab === "showcase");
        imageCard.classList.toggle("hidden", activeTab !== "image");
        imageResultsSection.classList.toggle("hidden", activeTab !== "image");
        titleCard.classList.toggle("hidden", activeTab !== "title");
        showcaseCard.classList.toggle("hidden", activeTab !== "showcase");
        const titles = { image: "图片识别", title: "标题分类", showcase: "图片合成" };
        if (window.Shell && Shell.setHeader) Shell.setHeader({ title: titles[activeTab] });
      }
```

（`tabImageBtn` 等已删按钮后会是 `null`，故加 `&&` 守卫；其余 card 引用仍存在。）

- [ ] **Step 4: 删除旧 tab 按钮点击监听，改为 shell 路由驱动**

找到为 `tabImageBtn/tabTitleBtn/tabShowcaseBtn` 绑定 `click` 的代码（在 `setActiveTab` 定义附近或事件绑定区，用 `grep -n 'tabImageBtn\|tabTitleBtn\|tabShowcaseBtn' web/index.html` 定位）。删除这些 `addEventListener("click", ...)` 调用。

在内联脚本中、`setActiveTab` 已定义之后，新增 shell 挂载：

```javascript
      // ---------- Shell + sidebar routing ----------
      Shell.mount({
        page: "test",
        defaultRoute: "image",
        brand: { logo: "M", text: "Mercari 识别" },
        sidebar: () => [
          { id: "image", label: "图片识别" },
          { id: "title", label: "标题分类" },
          { id: "showcase", label: "图片合成" },
        ],
        onRouteChange: (route) => setActiveTab(route),
      });
```

> 若原代码在初始化时直接调用过 `setActiveTab("image")`，删掉该直接调用——改由 `Shell.mount` 的初始 `onRouteChange` 触发，避免重复。

- [ ] **Step 5: 引入 shell.js（脚本顺序）**

在 `<body>` 末尾、现有内联 `<script>` **之前**加：

```html
    <script src="/assets/shell.js"></script>
```

确认 lightbox 等浮层元素的 id 若为 `lightbox`，已在 `shell.js` 的 `OVERLAY_IDS` 中（已含 `lightbox`），不会被搬进主区。用 `grep -n 'class="lightbox"\|id="lightbox"' web/index.html` 核对；若浮层用的是 class 而非 id，给它补一个 `id="lightbox"` 或确认它是动态创建（动态创建则无需处理）。

- [ ] **Step 6: 跑后端测试（含此前依赖 Task5 的断言）**

Run: `.venv/bin/python -m pytest tests/test_console_routes.py -v`
Expected: 全部 PASS（`test_index_served_with_password` 现在能在返回 HTML 里找到 `shell.js`）。

- [ ] **Step 7: 浏览器验证（Playwright）**

打开 `http://localhost:8000/`（Basic `a:secret`）：
- Expected: 顶栏 + 侧边栏；一级入口「配置 / 测试」，测试下有「图片识别 / 标题分类 / 图片合成」；默认图片识别，标题「图片识别」。
- 切「标题分类」「图片合成」→ 对应卡片显隐正确，标题随之更新。
- 「识别结果 / 测试统计 / 接口地址」卡片在主区下方持续可见。
- 点一级入口「配置」→ 跳到 `/config`，同会话不再弹密码框。

- [ ] **Step 8: Commit**

```bash
git add web/index.html tests/test_console_routes.py
git commit -m "feat(web): wire test page into shell sidebar (image/title/showcase)"
```

---

## Task 6: 端到端回归与日志页不受影响验证

**Files:** 无（验证任务）

- [ ] **Step 1: 全量后端测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿（或仅与本次无关的既有跳过项）。

- [ ] **Step 2: 日志页未受影响**

Playwright 打开 `http://localhost:8000/logs`（Basic `a:secret`）：
- Expected: 仍是原有 GitHub 风格日志查看器；**无**新侧边栏 chrome；工具栏、表格、详情展开、图片灯箱均正常。

- [ ] **Step 3: 鉴权边界**

- 打开 `http://localhost:8000/`（不带密码）→ Expected: 401 弹窗。
- 直接访问 `http://localhost:8000/assets/shell.js` → Expected: 200（无需密码）。

- [ ] **Step 4: 关闭本地服务**

```bash
kill %1 2>/dev/null || true
```

- [ ] **Step 5: 收尾 Commit（若有验证期间的小修）**

```bash
git add -A
git commit -m "test: end-to-end verification of console sidebar upgrade" || echo "nothing to commit"
```

---

## Self-Review 结论

- **Spec 覆盖**：①共享 chrome → Task1/2；②`/assets` 挂载 + `/` 加锁 → Task3；③配置页接入侧边栏 → Task4；④测试页接入侧边栏 + 密码保护 → Task5；⑤日志页不动、不进侧边栏 → 仅在 Task6 验证未受影响；⑥已知边界（Netlify 静态不拦截）已在 spec 记录，无需代码任务。全部有对应任务。
- **占位符**：无 TBD/TODO；所有代码步骤含完整代码。
- **命名一致**：`Shell.mount/setHeader/navigate/refreshSidebar`、`page:'config'|'test'`、路由 id `api/prompts/image/title/showcase`、`OVERLAY_IDS` 含 `lightbox`、后端 `ASSETS_DIR`/`/assets` 全程一致。
- **已知顺序依赖**：`test_index_served_with_password` 断言 `shell.js` 字符串，需 Task5 完成后才整体转绿（已在 Task3 注明，Task5 Step6 复跑）。
