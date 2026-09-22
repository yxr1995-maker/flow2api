import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.protocol_login import _classify_rejected_body, _classify_hop, _extract_oauth_error


def check(name, actual, expected):
    passed = actual == expected
    print("PASS" if passed else "FAIL", name, repr(actual))
    return passed


ok = True
ok &= check("browser_not_secure", _classify_rejected_body("signin/rejected ... This Browser or App may not be Secure ..."), "browser_not_secure")
ok &= check("javascript_required", _classify_rejected_body("signin/rejected ... JAVASCRIPT IS REQUIRED to proceed ..."), "javascript_required")
ok &= check("cookies_required", _classify_rejected_body("signin/rejected ... COOKIES ARE REQUIRED now ..."), "cookies_required")
ok &= check("rejected_unspecified", _classify_rejected_body("signin/rejected ... unknown layout ..."), "rejected_unspecified")
ok &= check("non_match_default", _classify_rejected_body("welcome page"), "rejected_unspecified")
ok &= check("non_str_default", _classify_rejected_body(None), "rejected_unspecified")

out = _classify_rejected_body("signin/rejected SID=SYNTHETIC-SECRET-1 user=SYNTHETIC-USER-1")
leak_free = out == "rejected_unspecified" and "SYNTHETIC-SECRET-1" not in out and "SYNTHETIC-USER-1" not in out
print("PASS" if leak_free else "FAIL", "no_secret_echo", repr(out))
ok &= leak_free

ok &= check("exact_rejected", _classify_hop("https://accounts.google.com/v3/signin/rejected?continue=https://x"), "signin_rejected")
ok &= check("exact_identifier_v2", _classify_hop("https://accounts.google.com/signin/v2/identifier?flowName=x"), "signin_identifier")
ok &= check("exact_identifier_v3", _classify_hop("https://accounts.google.com/v3/signin/identifier?hl=en"), "signin_identifier")
ok &= check("rejected_v2", _classify_hop("https://accounts.google.com/signin/rejected?continue=1"), "signin_rejected")
ok &= check("rejected_v2pwd", _classify_hop("https://accounts.google.com/signin/v2/rejected?continue=1"), "signin_rejected")
ok &= check("oauth2_auth", _classify_hop("https://accounts.google.com/o/oauth2/auth?client_id=x"), "oauth2_auth")
ok &= check("oauth2_v2_auth", _classify_hop("https://accounts.google.com/o/oauth2/v2/auth?client_id=x"), "oauth2_auth")
ok &= check("oauth_error", _classify_hop("https://accounts.google.com/signin/oauth/error?e=x"), "oauth_error")
ok &= check("substring_not_confused", _classify_hop("https://accounts.google.com/signin/v2"), "other")
ok &= check("consent_not_endswith", _classify_hop("https://accounts.google.com/signin/rejected_extra"), "other")
ok &= check("host_guard", _classify_hop("https://evil.test/v3/signin/rejected"), "other")
ok &= check("wp_known_error", _extract_oauth_error("https://accounts.google.com/o/oauth2/token?error=access_denied"), "access_denied")
ok &= check("wp_unknown_error", _extract_oauth_error("https://accounts.google.com/o/oauth2/token?error=SUPER_SECRET_VALUE"), "unknown")
ok &= check("wp_secret_error_not_echoed", _extract_oauth_error("https://accounts.google.com/error?error=SID%3DSECRET-9"), "unknown")
ok &= check("wp_known_unknown_host", _extract_oauth_error("https://evil.test/?error=access_denied"), "unknown")
ok &= check("wp_query_with_code", _extract_oauth_error("https://accounts.google.com/oauth2/auth?code=ONE_TIME_CODE&error=access_denied"), "access_denied")
ok &= check("wp_code_only_unknown", _extract_oauth_error("https://accounts.google.com/oauth2/auth?code=ONE-TIME-ABC"), "unknown")


print("ALL_PASS" if ok else "HAS_FAIL")
sys.exit(0 if ok else 1)
