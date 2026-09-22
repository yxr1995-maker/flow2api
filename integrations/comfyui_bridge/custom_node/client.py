import os
import json
import re
import urllib.request
import urllib.error
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional, Tuple

CONFIG_PATH = os.path.expanduser("~/.config/flow2api/comfy-connection.json")
# Single media-URL extractor: markdown image ![..](url) or <video src="url">.
MEDIA_URL_RE = re.compile(
    r'![^\]]*\]\(\s*([^\s)]+)\s*\)|<video[^>]+src=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disallows HTTP redirects to prevent authorization header leakage."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            newurl, code, f"Redirect to {newurl} is forbidden for security.", headers, fp
        )

def get_connection_config() -> Tuple[str, str]:
    """Reads base_url and api_key strictly on server side.
    Priority:
    1. Environment variables: FLOW2API_BASE_URL, FLOW2API_KEY
    2. File: ~/.config/flow2api/comfy-connection.json
    """
    env_base = os.environ.get("FLOW2API_BASE_URL")
    env_key = os.environ.get("FLOW2API_KEY")
    if env_base and env_key:
        return env_base.rstrip("/"), env_key

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                base = env_base or data.get("base_url", "http://127.0.0.1:8000")
                key = env_key or data.get("api_key", "")
                return base.rstrip("/"), key
        except Exception as e:
            raise RuntimeError(f"Failed to read flow2api connection config: {type(e).__name__}")

    if env_base:
        return env_base.rstrip("/"), env_key or ""

    raise RuntimeError(
        "flow2api connection configuration not found. "
        "Set env vars FLOW2API_BASE_URL + FLOW2API_KEY, "
        "or write {\"base_url\": ..., \"api_key\": ...} to ~/.config/flow2api/comfy-connection.json"
    )

def parse_sse_stream(response_stream) -> Dict[str, Any]:
    """Parses OpenAI-compatible SSE stream from flow2api and extracts result media URLs without leaking raw sensitive data."""
    full_content = ""
    error_msg = None
    media_url = None

    for line in response_stream:
        if not line:
            continue
        line_str = line.decode("utf-8", errors="ignore").strip()
        if not line_str.startswith("data:"):
            continue
        payload_str = line_str[5:].strip()
        if payload_str == "[DONE]":
            break
        try:
            payload = json.loads(payload_str)
            if "error" in payload:
                err = payload["error"]
                if isinstance(err, dict):
                    error_msg = err.get("message") or "flow2api returned an error."
                else:
                    error_msg = str(err)
            choices = payload.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content") or delta.get("reasoning_content") or ""
                full_content += content
                finish_reason = choices[0].get("finish_reason")
                if finish_reason == "error":
                    error_msg = "Generation terminated with error status."
        except json.JSONDecodeError:
            continue  # skip non-JSON heartbeat lines

    if error_msg:
        # Never surface raw backend text (may carry URLs/keys); stable type only.
        raise RuntimeError("flow2api generation failed.")

    # First media URL wins, markdown image or <video> tag.
    m = MEDIA_URL_RE.search(full_content)
    if m:
        media_url = (m.group(1) or m.group(2)).strip()

    return {
        "content": full_content,
        "media_url": media_url
    }

def call_flow2api_stream(model: str, messages: List[Dict[str, Any]], timeout: int = 1500) -> Dict[str, Any]:
    """Executes a streaming request to flow2api /v1/chat/completions without redirects or retries."""
    base_url, api_key = get_connection_config()
    url = f"{base_url}/v1/chat/completions"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    req_body = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    data_bytes = json.dumps(req_body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )

    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(req, timeout=timeout) as response:
            return parse_sse_stream(response)
    except urllib.error.HTTPError as e:
        # Redact raw backend body, only output sanitized HTTP code
        raise RuntimeError(f"flow2api upstream HTTP {e.code} error (no auto-retry).")
    except urllib.error.URLError:
        raise RuntimeError("flow2api upstream network connection error (no auto-retry).")
    except (OSError, TimeoutError, ValueError):
        raise RuntimeError("flow2api generation failed (no auto-retry).")
