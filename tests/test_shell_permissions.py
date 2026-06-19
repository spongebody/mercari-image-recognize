import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL_JS = ROOT / "web" / "assets" / "shell.js"


def test_shell_fetches_identity_with_same_origin_credentials():
    source = SHELL_JS.read_text(encoding="utf-8")

    assert "/api/v1/console/me" in source
    assert "credentials: 'same-origin'" in source
    assert "userNode.textContent" in source
    assert ".innerHTML = identity.username" not in source
    assert ".innerHTML = me.username" not in source


def test_shell_menu_rendering_follows_loaded_identity(tmp_path):
    script = tmp_path / "shell_behavior_test.js"
    script.write_text(
        r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const shellPath = process.argv[2];
const shellSource = fs.readFileSync(shellPath, 'utf8');

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.className = '';
    this.id = '';
    this.textContent = '';
    this.hidden = false;
    this._innerHTML = '';
    this.classList = {
      add: (...names) => {
        const existing = new Set(this.className.split(/\s+/).filter(Boolean));
        names.forEach((name) => existing.add(name));
        this.className = Array.from(existing).join(' ');
      },
    };
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
    this.children = [];
  }

  get innerHTML() {
    return this._innerHTML;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'id') this.id = String(value);
    if (name === 'class') this.className = String(value);
  }

  addEventListener() {}

  append(...nodes) {
    nodes.forEach((node) => {
      if (!node) return;
      node.parentNode = this;
      this.children.push(node);
    });
  }

  appendChild(node) {
    this.append(node);
    return node;
  }

  prepend(...nodes) {
    nodes.reverse().forEach((node) => {
      if (!node) return;
      node.parentNode = this;
      this.children.unshift(node);
    });
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((node) => node !== this);
    this.parentNode = null;
  }

  querySelector(selector) {
    if (!selector.startsWith('.')) return null;
    const className = selector.slice(1);
    return findFirst(this, (node) => hasClass(node, className));
  }
}

function hasClass(node, className) {
  return node.className.split(/\s+/).includes(className);
}

function walk(node, visitor) {
  for (const child of node.children) {
    visitor(child);
    walk(child, visitor);
  }
}

function findFirst(node, predicate) {
  for (const child of node.children) {
    if (predicate(child)) return child;
    const nested = findFirst(child, predicate);
    if (nested) return nested;
  }
  return null;
}

function createDocument() {
  const body = new Element('body');
  body.appendChild(new Element('div'));
  return {
    body,
    createElement: (tag) => new Element(tag),
    getElementById: (id) => findFirst(body, (node) => node.id === id),
  };
}

function createShell(fetchImpl) {
  const context = {
    console,
    document: createDocument(),
    fetch: fetchImpl,
    history: {
      replaceState: (_state, _title, url) => {
        context.location.hash = String(url).startsWith('#') ? String(url) : '';
      },
    },
    location: {
      hash: '',
      replace: (url) => {
        context.location.replacedWith = url;
      },
    },
    addEventListener() {},
    Set,
    Array,
    String,
    Error,
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(shellSource, context, { filename: shellPath });
  return context;
}

function navLabels(context) {
  const sidebar = context.document.getElementById('shellSidebar');
  const labels = [];
  walk(sidebar, (node) => {
    if (node.tagName === 'A' && hasClass(node, 'nav-group')) {
      labels.push(node.children[1].textContent);
    }
  });
  return labels;
}

function userText(context) {
  return context.document.body.querySelector('.shell-user').textContent;
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function testSuccessPath() {
  const fetchCalls = [];
  let resolveMe;
  const context = createShell((url, options) => {
    fetchCalls.push({ url, options });
    return new Promise((resolve) => {
      resolveMe = resolve;
    });
  });

  context.Shell.mount({
    page: 'evaluations',
    defaultRoute: 'runs',
    sidebar: () => [{ id: 'runs', label: 'Runs' }],
  });

  assert.deepEqual(navLabels(context), ['模型测试']);
  assert.equal(fetchCalls[0].url, '/api/v1/console/me');
  assert.equal(fetchCalls[0].options.credentials, 'same-origin');

  resolveMe({
    ok: true,
    json: async () => ({ username: 'tester<bad>', menus: ['evaluations', 'logs'] }),
  });
  await flush();
  await flush();

  assert.deepEqual(navLabels(context), ['模型测试', '日志']);
  assert.equal(userText(context), 'tester<bad>');
}

async function testFailurePath() {
  const context = createShell(() => Promise.resolve({ ok: false, json: async () => ({}) }));

  context.Shell.mount({
    page: 'accounts',
    defaultRoute: 'accounts',
    sidebar: () => [{ id: 'accounts', label: '账号管理' }],
  });
  await flush();
  await flush();

  assert.deepEqual(navLabels(context), ['账号管理']);
  assert.equal(userText(context), '');
}

(async () => {
  await testSuccessPath();
  await testFailurePath();
})();
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(script), str(SHELL_JS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
