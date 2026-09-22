import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from src.services.protocol_login import (
    _parse_google_cookie_record, _seed_google_cookies,
    _validate_login_url, _is_login_callback,
)


def record(name, value, domain, path="/", host_only=True):
    return dict(name=name, value=value, domain=domain, path=path,
                secure=False, hostOnly=host_only)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        if self.path == "/rotate":
            self.send_header("Set-Cookie", "SID=rotated; Path=/")
        elif self.path == "/delete":
            self.send_header("Set-Cookie", "SID=; Max-Age=0; Path=/")
        self.end_headers()
        self.wfile.write((self.headers.get("Cookie") or "").encode())


async def main():
    base = record("SID", "padded==", "accounts.google.com")
    assert _parse_google_cookie_record(base)["value"] == "padded=="
    assert _parse_google_cookie_record({**base, "domain": ".accounts.google.com"})["domain"] == "accounts.google.com"
    for bad in [None, {**base, "secure": "false"}, {**base, "hostOnly": "true"},
                {**base, "domain": "google.com.evil.test"}, {**base, "domain": "..google.com"},
                {**base, "path": "relative"}, {**base, "name": "bad name"},
                {**base, "value": "secret\r\nInjected: x"}, {**base, "value": "x;y"}]:
        try:
            _parse_google_cookie_record(bad)
        except ValueError as exc:
            assert str(exc) == "invalid google cookie payload"
        else:
            raise AssertionError("invalid cookie accepted")
    callback = "https://labs.google/fx/api/auth/callback/google?code=synthetic"
    assert _is_login_callback(callback)
    for url in ["http://accounts.google.com/", "https://accounts.google.com:444/",
                "https://user@accounts.google.com/", "https://evil.test/",
                "https://evil.test/labs.google/fx/api/auth/callback/google"]:
        try:
            _validate_login_url(url)
        except ValueError as exc:
            assert str(exc) == "UNEXPECTED_AUTH_REDIRECT"
        else:
            raise AssertionError("unsafe redirect accepted")
        assert not _is_login_callback(url)
    print("PASS cookie validation and redirect boundary")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    hosts = ["accounts.google.com", "flow.google.com", "sub.accounts.google.com", "labs.google"]
    options = {CurlOpt.RESOLVE: [f"{host}:{port}:127.0.0.1" for host in hosts], CurlOpt.NOPROXY: "*"}
    try:
        async with AsyncSession(trust_env=False, curl_options=options) as session:
            entries = [record("SID", "initial", "accounts.google.com"),
                       record("SID", "flow-only", "flow.google.com"),
                       record("SID", "path-only", "accounts.google.com", "/special"),
                       record("OSID", "flow-session", "flow.google.com")]
            _seed_google_cookies(session, {"SID": "ignored"}, json.dumps(entries))
            assert len(list(session.cookies.jar)) == 4
            async def get(host, path="/"):
                return (await session.get(f"http://{host}:{port}{path}", allow_redirects=False, timeout=5)).text
            assert await get("accounts.google.com") == "SID=initial"
            assert "OSID=flow-session" in await get("flow.google.com")
            assert await get("sub.accounts.google.com") == ""
            assert await get("labs.google") == ""
            assert "SID=path-only" in await get("accounts.google.com", "/special")
            print("PASS same-name domain/path preservation and host-only isolation")
            await get("accounts.google.com", "/rotate")
            assert await get("accounts.google.com") == "SID=rotated"
            await get("accounts.google.com", "/delete")
            assert await get("accounts.google.com") == ""
            old = await session.get(f"http://accounts.google.com:{port}/", headers={"Cookie": "SID=initial"}, timeout=5)
            assert old.text == "SID=initial"
            print("PASS real curl rotation/deletion; old fixed header replays deleted cookie")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    asyncio.run(main())
