from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL_JS = ROOT / "web" / "assets" / "shell.js"


def test_shell_filters_menu_from_console_me():
    source = SHELL_JS.read_text(encoding="utf-8")

    assert "/api/v1/console/me" in source
    assert "credentials: 'same-origin'" in source
    assert "allowedPages" in source
    assert ".filter(" in source
    assert "id: 'accounts'" in source
    assert "id: 'logs'" in source
    assert "id: 'config'" in source
    assert "id: 'test'" in source
    assert "id: 'evaluations'" in source


def test_shell_user_is_rendered_without_innerhtml_interpolation():
    source = SHELL_JS.read_text(encoding="utf-8")

    assert "shell-user" in source
    assert "username" in source
    assert "userNode.textContent" in source
    assert ".innerHTML = identity.username" not in source
    assert ".innerHTML = me.username" not in source
