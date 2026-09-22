import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi import HTTPException as HTTPError

import src.api.admin as admin
from src.api.admin import plugin_update_token

out = []


def check(name, cond, extra=""):
    out.append(("PASS" if cond else "FAIL") + " " + name + (" | " + str(extra) if extra else ""))
    return cond


async def main():
    admin._verify_plugin_connection_token = AsyncMock(return_value=None)
    existing = SimpleNamespace(id=9, is_active=True)
    admin.db = SimpleNamespace(
        get_plugin_config=AsyncMock(return_value=SimpleNamespace(auto_enable_on_update=False)),
        get_token_by_email=AsyncMock(return_value=existing),
        add_token=AsyncMock(return_value=SimpleNamespace(id=9, email="t@example.com")),
        update_token=AsyncMock(),
    )
    admin.token_manager = SimpleNamespace(
        flow_client=SimpleNamespace(st_to_at=AsyncMock(return_value={
            "access_token": "AT", "expires": None, "user": {"email": "t@example.com"}})),
        update_token=AsyncMock(),
        add_token=AsyncMock(return_value=SimpleNamespace(id=9, email="t@example.com")),
        enable_token=AsyncMock(),
    )
    admin.protocol_loginer.login = AsyncMock(return_value={"success": True, "session_token": "ST"})
    admin.proxy_manager = SimpleNamespace(get_request_proxy_url=AsyncMock(return_value="http://p"))

    r = await plugin_update_token({"session_token": "ST_ABC"}, "auth")
    check("session update success", r.get("action") == "updated", r)
    check("login not called", admin.protocol_loginer.login.await_count == 0, admin.protocol_loginer.login.await_count)
    check("proxy not called", admin.proxy_manager.get_request_proxy_url.await_count == 0, admin.proxy_manager.get_request_proxy_url.await_count)
    check("update mode session", admin.token_manager.update_token.call_args.kwargs.get("protocol_mode") == "session")

    for label, bad in (
        ("space rejected", "ST AB"),
        ("nul rejected", "ST\x00X"),
    ):
        try:
            await plugin_update_token({"session_token": bad}, "auth")
            check(label, False, "no raise")
        except HTTPError as e:
            check(label, getattr(e, "status_code", None) == 400)

    before_updated = admin.token_manager.update_token.await_count
    before_added = admin.token_manager.add_token.await_count
    admin.token_manager.flow_client.st_to_at = AsyncMock(side_effect=RuntimeError("SECRET_INTERNAL"))
    try:
        await plugin_update_token({"session_token": "abc"}, "auth")
        check("st_to_at fixed error", False, "no raise")
    except HTTPError as e:
        check("st_to_at fixed error",
              getattr(e, "status_code", None) == 400 and getattr(e, "detail", None) == "Invalid session token",
              str(getattr(e, "detail", None)))
    check("invalid st no update/add", admin.token_manager.update_token.await_count == before_updated and admin.token_manager.add_token.await_count == before_added)

    admin._verify_plugin_connection_token = AsyncMock(side_effect=HTTPError(status_code=401, detail="Invalid connection token"))
    try:
        await plugin_update_token({"session_token": "abc"}, "auth")
    except HTTPError as e:
        check("auth reject", getattr(e, "status_code", None) == 401)
    check("auth reject no login", admin.protocol_loginer.login.await_count == 0, admin.protocol_loginer.login.await_count)


asyncio.run(main())
print(chr(10).join(out))
if any(line.startswith("FAIL") for line in out):
    raise SystemExit(1)

