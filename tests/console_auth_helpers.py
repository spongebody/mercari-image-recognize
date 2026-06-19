def auth_headers(password: str | None = None) -> dict:
    if password is None:
        import main

        password = main.settings.logs_password or "testpass"
    return {"Authorization": f"Bearer {password}"}
