import subprocess


def test_console_users_json_is_gitignored():
    result = subprocess.run(
        ["git", "check-ignore", "-q", "data/console_users.json"],
        check=False,
    )
    assert result.returncode == 0
