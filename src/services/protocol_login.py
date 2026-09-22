"""Protocol login for labs.google NextAuth with exported Google cookies."""

import json
import http.cookiejar
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote

from curl_cffi.requests import AsyncSession

from ..core.logger import debug_logger


LABS_BASE = "https://labs.google/fx"
SESSION_COOKIE_NAME = "__Secure-next-auth.session-token"
IMPERSONATE = "chrome124"
GOOGLE_COOKIE_NAMES = ("SID", "HSID", "SSID", "APISID", "SAPISID")
COOKIE_RECORD_DOMAINS = frozenset({"google.com", "accounts.google.com", "flow.google.com"})
OAUTH_REDIRECT_HOSTS = frozenset({"google.com", "accounts.google.com"})
LABS_HOST = "labs.google"
LABS_CALLBACK_PATH = "/fx/api/auth/callback/google"


_REJECTED_BROWSER_NOT_SECURE = "this browser or app may not be secure"
_REJECTED_JAVASCRIPT_REQUIRED = "javascript is required"
_REJECTED_COOKIES_REQUIRED = "cookies are required"


def _classify_rejected_body(body: object) -> str:
    text = body if isinstance(body, str) else ""
    t = text.lower()
    if _REJECTED_BROWSER_NOT_SECURE in t:
        return "browser_not_secure"
    if _REJECTED_JAVASCRIPT_REQUIRED in t:
        return "javascript_required"
    if _REJECTED_COOKIES_REQUIRED in t:
        return "cookies_required"
    return "rejected_unspecified"


def _parse_google_cookies(raw: str) -> Dict[str, str]:
    """Parse Google cookies from JSON export or name=value text."""
    text = (raw or "").strip()
    if not text:
        return {}

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        data = None

    if isinstance(data, list):
        result: Dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name and value:
                result[name] = value
        if result:
            return result

    if isinstance(data, dict):
        cookies_list = data.get("cookies")
        if isinstance(cookies_list, list):
            result: Dict[str, str] = {}
            for item in cookies_list:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if name and value:
                    result[name] = value
            if result:
                return result

        result = {
            str(key).strip(): str(value).strip()
            for key, value in data.items()
            if isinstance(value, str) and str(key).strip() and value.strip()
        }
        if result:
            return result

    result: Dict[str, str] = {}
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name and value:
            result[name] = value
    return result


def _build_cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if name and value)


def _parse_google_cookie_record(item):
    if not isinstance(item, dict):
        raise ValueError("invalid google cookie payload")
    name = item.get("name")
    value = item.get("value")
    domain = item.get("domain")
    path = item.get("path")
    secure = item.get("secure", False)
    host_only = item.get("hostOnly", False)
    if not all(isinstance(x, str) for x in (name, value, domain, path)):
        raise ValueError("invalid google cookie payload")
    if not isinstance(secure, bool) or not isinstance(host_only, bool):
        raise ValueError("invalid google cookie payload")
    for field in (name, value, domain, path):
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in field):
            raise ValueError("invalid google cookie payload")
    name = name.strip()
    domain = domain.strip()
    if not name or len(domain) - len(domain.lstrip(".")) > 1:
        raise ValueError("invalid google cookie payload")
    if domain != domain.lower() or domain.lstrip(".") not in COOKIE_RECORD_DOMAINS:
        raise ValueError("invalid google cookie payload")
    if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
        raise ValueError("invalid google cookie payload")
    if any(ch in value for ch in (";", ",")):
        raise ValueError("invalid google cookie payload")
    if not path.startswith("/"):
        raise ValueError("invalid google cookie payload")
    if host_only:
        domain = domain.lstrip(".")
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "secure": secure,
        "host_only": host_only,
    }


def _parse_google_cookie_records(raw):
    try:
        data = json.loads((raw or "").strip() or "null")
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict):
        data = data.get("cookies")
    if not isinstance(data, list) or len(data) == 0:
        return None
    return [_parse_google_cookie_record(item) for item in data]


def _make_jar_cookie(name, value, domain, path, secure, host_only):
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=not host_only,
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=secure,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _seed_google_cookies(session, cookies, raw=None):
    jar = session.cookies.jar
    records = _parse_google_cookie_records(raw)
    if records is not None:
        for cookie in records:
            jar.set_cookie(_make_jar_cookie(
                cookie["name"], cookie["value"], cookie["domain"], cookie["path"],
                cookie["secure"], cookie["host_only"],
            ))
        return
    for name, value in cookies.items():
        if not name or not value:
            continue
        for field in (name, value):
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in field):
                raise ValueError("invalid google cookie payload")
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise ValueError("invalid google cookie payload")
        if any(ch in value for ch in (";", ",")):
            raise ValueError("invalid google cookie payload")
        if name in ("OSID", "__Secure-OSID"):
            domain = "flow.google.com"
            host_only = True
        else:
            domain = ".google.com"
            host_only = False
        jar.set_cookie(_make_jar_cookie(name, value, domain, "/", True, host_only))


def _get_set_cookies(headers: Any) -> List[str]:
    if hasattr(headers, "getlist"):
        return headers.getlist("set-cookie") or headers.getlist("Set-Cookie") or []
    if hasattr(headers, "get_list"):
        return headers.get_list("set-cookie") or headers.get_list("Set-Cookie") or []
    value = headers.get("set-cookie") or headers.get("Set-Cookie")
    return [value] if value else []


def _merge_cookies(cookies: Dict[str, str], headers: Any) -> None:
    for line in _get_set_cookies(headers):
        first = str(line).split(";", 1)[0].strip()
        if "=" not in first:
            continue
        name, _, value = first.partition("=")
        if name.strip():
            cookies[name.strip()] = value.strip()


def _extract_session_token(headers: Any) -> Optional[str]:
    for line in _get_set_cookies(headers):
        first = str(line).split(";", 1)[0].strip()
        if not first.startswith(f"{SESSION_COOKIE_NAME}="):
            continue
        return first.split("=", 1)[1].strip()
    return None


def _extract_redirect_from_html(text: str) -> Optional[str]:
    body = text or ""
    match = re.search(
        r'content\s*=\s*["\']?\d+\s*;\s*url\s*=\s*([^"\'>\s]+)',
        body,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.search(
        r'location(?:\.(?:href|replace))?\s*(?:\(|=)\s*["\']([^"\']+)',
        body,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.search(r'<form[^>]*action\s*=\s*["\']([^"\']+)', body, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(r'(https://labs\.google/fx/api/auth/callback/google[^"\'<>\s]*)', body)
    if match:
        return match.group(1)

    match = re.search(r'[&?]continue=([^"\'<>\s&]+)', body)
    if match:
        return unquote(match.group(1))

    return None


def _normalize_proxy_url(proxy_url: Optional[str]) -> Optional[str]:
    raw = (proxy_url or "").strip()
    if not raw:
        return None

    st5_match = re.match(r"^st5\s+(.+)$", raw, re.IGNORECASE)
    if st5_match:
        rest = st5_match.group(1).strip()
        if "@" in rest:
            return f"socks5://{rest}"
        parts = rest.split(":")
        if len(parts) >= 4 and parts[1].isdigit():
            return f"socks5://{parts[2]}:{':'.join(parts[3:])}@{parts[0]}:{parts[1]}"
        return None

    if "://" not in raw:
        if "@" in raw:
            return f"http://{raw}"
        parts = raw.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            return f"http://{parts[0]}:{parts[1]}"
        if len(parts) >= 4 and parts[1].isdigit():
            return f"http://{parts[2]}:{':'.join(parts[3:])}@{parts[0]}:{parts[1]}"
        return None

    if re.match(r"^(http|https|socks5h?|socks5)://", raw, re.IGNORECASE):
        if "@" in raw:
            return raw
        scheme, _, rest = raw.partition("://")
        parts = rest.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            return raw
        if len(parts) >= 4 and parts[1].isdigit():
            return f"{scheme}://{parts[2]}:{':'.join(parts[3:])}@{parts[0]}:{parts[1]}"
    return None


def _append_login_hint(target_url: str, email: Optional[str]) -> str:
    email = (email or "").strip()
    if not email:
        return target_url
    parsed = urlparse(target_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["login_hint"] = email
    return urlunparse(parsed._replace(query=urlencode(query)))


def _validate_login_url(url: str, *, labs: bool = False, callback: bool = False) -> None:
    """Reject redirects outside the authentication origins before sending cookies."""
    try:
        parsed = urlparse(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in ({LABS_HOST} if labs or callback else OAUTH_REDIRECT_HOSTS)
            and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and not any(ord(char) < 33 for char in url)
            and (not callback or parsed.path == LABS_CALLBACK_PATH)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("UNEXPECTED_AUTH_REDIRECT")


def _is_login_callback(url: str) -> bool:
    try:
        _validate_login_url(url, callback=True)
        return True
    except ValueError:
        return False


KNOWN_OAUTH_ERRORS = frozenset({
    "access_denied", "canceled", "user_canceled", "invalid_request",
    "unauthorized", "unsupported_response_type", "server_error",
    "temporarily_disabled", "disallowed_user_agent", "access_denied_cancel",
})

HOP_PATH_ENUM = {
    "/v3/signin/rejected": "signin_rejected",
    "/signin/rejected": "signin_rejected",
    "/signin/v2/rejected": "signin_rejected",
    "/signin/v2/identifier": "signin_identifier",
    "/v3/signin/identifier": "signin_identifier",
    "/signin/v2/challenge/pwd": "signin_challenge",
    "/o/oauth2/auth": "oauth2_auth",
    "/o/oauth2/v2/auth": "oauth2_auth",
    "/signin/oauth/error": "oauth_error",
}


def _extract_oauth_error(url: str) -> str:
    """Return one whitelisted OAuth error enum from the URL, else 'unknown'."""
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return "unknown"
    if parsed.hostname not in OAUTH_REDIRECT_HOSTS:
        return "unknown"
    error_vals = [v for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k == "error"]
    if error_vals and error_vals[0] in KNOWN_OAUTH_ERRORS:
        return error_vals[0]
    return "unknown"


def _classify_hop(url: str) -> str:
    """Return the fixed enum for a known Google auth path, else 'other'."""
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return "other"
    if parsed.hostname not in OAUTH_REDIRECT_HOSTS:
        return "other"
    return HOP_PATH_ENUM.get(parsed.path, "other")


class ProtocolLogin:
    """Login to labs.google/fx through NextAuth Google OAuth using Google cookies."""

    async def login(
        self,
        google_cookies_raw: str,
        proxy: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        google_cookies = _parse_google_cookies(google_cookies_raw)
        if not any(name in google_cookies for name in GOOGLE_COOKIE_NAMES):
            return {
                "success": False,
                "error": "未找到有效的 Google cookie（需要 SID/HSID/SSID/APISID/SAPISID 中至少一个）",
            }

        session_kwargs: Dict[str, Any] = {"impersonate": IMPERSONATE, "trust_env": False}
        proxy_url = _normalize_proxy_url(proxy)
        if proxy_url:
            session_kwargs["proxy"] = proxy_url

        async with AsyncSession(**session_kwargs) as session:
            try:
                csrf_resp = await session.get(f"{LABS_BASE}/api/auth/csrf")
                if csrf_resp.status_code != 200:
                    return {"success": False, "error": f"CSRF 失败: HTTP {csrf_resp.status_code}"}
                csrf_token = (csrf_resp.json() or {}).get("csrfToken")
                if not csrf_token:
                    return {"success": False, "error": "CSRF 响应缺少 csrfToken"}

                labs_cookies: Dict[str, str] = {}
                _merge_cookies(labs_cookies, csrf_resp.headers)

                signin_resp = await session.post(
                    f"{LABS_BASE}/api/auth/signin/google",
                    data={
                        "csrfToken": csrf_token,
                        "callbackUrl": LABS_BASE,
                        "json": "true",
                    },
                    headers={
                        "Referer": LABS_BASE,
                        "Origin": "https://labs.google",
                        "Cookie": _build_cookie_header(labs_cookies),
                    },
                    allow_redirects=False,
                )
                if signin_resp.status_code != 200:
                    return {"success": False, "error": f"Signin 失败: HTTP {signin_resp.status_code}"}

                _merge_cookies(labs_cookies, signin_resp.headers)
                signin_data = signin_resp.json() or {}
                redirect_url = signin_data.get("redirect") or signin_data.get("url")
                if not redirect_url:
                    return {"success": False, "error": "登录响应缺少重定向 URL"}
                redirect_url = _append_login_hint(redirect_url, email)

                _seed_google_cookies(session, google_cookies, raw=google_cookies_raw)
                callback_url = ""
                current_url = redirect_url

                for attempt in range(10):
                    _validate_login_url(current_url)
                    oauth_resp = await session.get(
                        current_url,
                        headers={
                            "Referer": "https://labs.google/" if attempt == 0 else "https://accounts.google.com/",
                        },
                        allow_redirects=False,
                    )
                    location = (oauth_resp.headers.get("location") or "").strip()
                    if location:
                        location = urljoin(current_url, location)
                        if _is_login_callback(location):
                            callback_url = location
                            break
                        current_url = location
                        continue

                    body = oauth_resp.text or ""
                    page = _classify_hop(current_url)
                    oauth_err = _extract_oauth_error(current_url)
                    if "signin/rejected" in body.lower():
                        reason = _classify_rejected_body(body)
                        try:
                            http_code = int(oauth_resp.status_code)
                        except (TypeError, ValueError):
                            http_code = -1
                        debug_logger.log_error(f"[PROTOCOL_LOGIN] auth-rejected page={page} oauth_error={oauth_err}")
                        return {"success": False, "error": f"Google 拒绝登录（HTTP {http_code}，hop={attempt + 1}，page={page}，reason={reason}，oauth_error={oauth_err}）"}

                    if oauth_resp.status_code == 200:
                        html_redirect = _extract_redirect_from_html(body)
                        if html_redirect:
                            html_redirect = urljoin(current_url, html_redirect)
                            if _is_login_callback(html_redirect):
                                callback_url = html_redirect
                                break
                            current_url = html_redirect
                            continue

                    return {
                        "success": False,
                        "error": f"Google OAuth 未返回重定向（HTTP {oauth_resp.status_code}）",
                    }

                if not callback_url:
                    return {"success": False, "error": "Google OAuth 流程中未获得 callback URL"}

                _validate_login_url(callback_url, callback=True)
                callback_resp = await session.get(
                    callback_url,
                    headers={
                        "Cookie": _build_cookie_header(labs_cookies),
                        "Referer": "https://accounts.google.com/",
                    },
                    allow_redirects=False,
                )

                session_token = _extract_session_token(callback_resp.headers)
                _merge_cookies(labs_cookies, callback_resp.headers)

                for _ in range(5):
                    if session_token:
                        break
                    location = (callback_resp.headers.get("location") or "").strip()
                    if not location or callback_resp.status_code not in (301, 302, 303, 307, 308):
                        break
                    callback_url = urljoin(callback_url, location)
                    _validate_login_url(callback_url, labs=True)
                    callback_resp = await session.get(
                        callback_url,
                        headers={"Cookie": _build_cookie_header(labs_cookies)},
                        allow_redirects=False,
                    )
                    _merge_cookies(labs_cookies, callback_resp.headers)
                    session_token = _extract_session_token(callback_resp.headers)

                if not session_token:
                    return {"success": False, "error": "未获取到 session token，Google session 可能已过期"}
                return {"success": True, "session_token": session_token}
            except Exception as exc:
                error = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                    "invalid google cookie payload", "UNEXPECTED_AUTH_REDIRECT"
                } else "GOOGLE_PROTOCOL_REQUEST_FAILED"
                debug_logger.log_error(f"[PROTOCOL_LOGIN] {error}")
                return {"success": False, "error": error}


protocol_loginer = ProtocolLogin()
