/* Shared console chrome for console pages.
 *
 * Usage:
 *   Shell.mount({
 *     page: 'config',                 // 'config' | 'test' | 'evaluations' | 'logs' | 'accounts'
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
    { id: 'config',      label: '配置', href: '/config' },
    { id: 'test',        label: '测试', href: '/' },
    { id: 'evaluations', label: '模型测试', href: '/evaluations' },
    { id: 'logs',        label: '日志', href: '/logs' },
    { id: 'accounts',    label: '账号管理', href: '/accounts' },
  ];

  // Body-level overlays that must NOT be moved into the shell main area.
  const OVERLAY_IDS = new Set(['lightbox', 'image-lightbox', 'evaluation-lightbox', 'toast', 'modalBackdrop']);

  let config = null;
  let identity = null;
  let userNode = null;

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
      el('span', { class: 'shell-user', 'aria-live': 'polite' }),
      el('button', {
        class: 'shell-logout',
        type: 'button',
        title: '退出登录 / ログアウト',
        'aria-label': '退出登录',
        html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg><span>退出</span>',
        onClick: logout,
      }),
    ]);

    const sidebar = el('aside', { id: 'shellSidebar', class: 'shell-sidebar' });
    const pageContent = el('div', { id: 'shellPageContent', class: 'page-content' });
    preserved.forEach((n) => pageContent.appendChild(n));
    const main = el('main', { id: 'shellMain', class: 'shell-main' }, [pageContent]);
    const body = el('div', { class: 'shell-body' }, [sidebar, main]);

    document.body.prepend(topbar);
    document.body.append(body);
    userNode = topbar.querySelector('.shell-user');
    renderUser();

    renderSidebar();
    loadIdentity();
    window.addEventListener('hashchange', onHashChange);
    if (!window.location.hash && config.defaultRoute) {
      history.replaceState(null, '', '#' + config.defaultRoute);
    }
    onHashChange();  // initial dispatch
  }

  async function loadIdentity() {
    try {
      const response = await fetch('/api/v1/console/me', { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Unable to load console identity.');
      const me = await response.json();
      identity = me;
      renderUser();
      renderSidebar();
    } catch (_) {
      identity = null;
      renderUser();
    }
  }

  function renderUser() {
    if (!userNode) return;
    const username = identity && identity.username ? String(identity.username) : '';
    userNode.textContent = username;
    userNode.hidden = !username;
  }

  function allowedPages() {
    if (!identity || !Array.isArray(identity.menus)) return PAGES;
    const menus = new Set(identity.menus);
    return PAGES.filter((page) => menus.has(page.id));
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

    for (const p of allowedPages()) {
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

  // Page title is conveyed by the left nav (active group + sub-item), so the
  // in-content page header was removed. Kept as a no-op for call-site
  // compatibility across pages that still invoke it.
  function setHeader() {}

  async function logout() {
    try {
      await fetch('/api/v1/console/logout', { method: 'POST', credentials: 'same-origin' });
    } finally {
      window.location.replace('/login');
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
