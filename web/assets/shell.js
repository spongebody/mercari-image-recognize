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
    { id: 'config',      label: '配置', href: '/config' },
    { id: 'test',        label: '测试', href: '/' },
    { id: 'evaluations', label: '模型测试', href: '/evaluations' },
  ];

  // Body-level overlays that must NOT be moved into the shell main area.
  const OVERLAY_IDS = new Set(['lightbox', 'image-lightbox', 'evaluation-lightbox', 'toast', 'modalBackdrop']);

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
