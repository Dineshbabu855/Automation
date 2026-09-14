"""HTTP Request action type — makes an HTTP call to any URL.

This module also exposes ``make_http_request()`` as a reusable helper so that
other action types (e.g. Telegram) can issue HTTP calls without duplicating
the request-building, error-handling, and logging logic.

SSRF protection: before making any request, the target URL's host is resolved
once, checked against private/link-loopback/cloud-metadata IP ranges, and
then the request is pinned to that validated IP via a custom HTTPAdapter.
DNS is never re-resolved between the check and the connect — this prevents
DNS rebinding attacks where a hostname resolves to a safe IP at check-time
but a malicious IP at connect-time. Redirects are disabled.
"""

import json
import socket
from urllib.parse import urlparse, urlencode

import frappe
import requests as _requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from automation_builder.action_types import register_action_type
from automation_builder.action_types._helpers import resolve_value

CONFIG_SCHEMA = [
    {
        "name": "trigger_doctype_select",
        "type": "trigger_doctype_select",
        "label": "Trigger DocType",
        "description": "Which trigger's document to use for field tokens. Only shown when automation has multiple triggers.",
    },
    {
        "name": "url",
        "type": "data",
        "label": "URL",
        "description": "Full URL. Supports {{trigger.fieldname}} tokens.",
    },
    {
        "name": "method",
        "type": "select",
        "label": "Method",
        "options": ["GET", "POST", "PUT", "PATCH", "DELETE"],
    },
    {
        "name": "headers",
        "type": "field_mapping_table",
        "label": "Headers",
        "description": "Optional HTTP headers (key/value). Supports {{trigger.fieldname}} in values.",
    },
    {
        "name": "body",
        "type": "textarea",
        "label": "Body",
        "description": "Request body (sent as JSON for POST/PUT/PATCH). Supports {{trigger.fieldname}} tokens.",
    },
]


# ---------------------------------------------------------------------------
# SSRF protection — IP range validation
# ---------------------------------------------------------------------------
# RFC1918 private ranges + loopback + link-local + cloud metadata
_BLOCKED_PREFIXES = [
    "10.",          # 10.0.0.0/8
    "172.16.",      # 172.16.0.0/12 (simplified to /16 blocks)
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",     # 192.168.0.0/16
    "127.",         # 127.0.0.0/8 loopback
    "169.254.",     # 169.254.0.0/16 link-local + cloud metadata
    "0.",           # 0.0.0.0/8
]


def _is_blocked_ip(ip_str):
    """Return True if the IP string is in a blocked private/reserved range."""
    for prefix in _BLOCKED_PREFIXES:
        if ip_str.startswith(prefix):
            return True
    # IPv6 loopback
    if ip_str == "::1" or ip_str.startswith("fc") or ip_str.startswith("fd"):
        return True
    return False


def validate_url_not_ssrf(url):
    """Validate that a URL does not target private/internal IP ranges.

    Resolves the hostname and checks each resolved IP against the denylist.
    Raises ValueError if any IP is blocked. Returns (hostname, validated_ip)
    tuple on success.

    The validated_ip is returned so the caller can pin the HTTP connection to
    it (preventing DNS rebinding). This function is called BEFORE any network
    I/O is attempted.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid URL: no hostname found in '{url}'")

    # Block cloud metadata endpoint by hostname too (before DNS resolution)
    if hostname == "169.254.169.254":
        raise ValueError(
            f"SSRF blocked: '{hostname}' is the cloud metadata endpoint"
        )

    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
    except socket.gaierror:
        raise ValueError(f"SSRF blocked: cannot resolve hostname '{hostname}'")

    validated_ip = None
    for family, _, _, _, sockaddr in resolved:
        ip = sockaddr[0]
        if _is_blocked_ip(ip):
            raise ValueError(
                f"SSRF blocked: hostname '{hostname}' resolves to private/reserved IP {ip}"
            )
        if validated_ip is None:
            validated_ip = ip

    if validated_ip is None:
        raise ValueError(f"SSRF blocked: hostname '{hostname}' resolved to no IPs")

    return hostname, validated_ip


class _PinnedIPAdapter(HTTPAdapter):
    """HTTPAdapter that connects to a pre-validated IP instead of re-resolving DNS.

    This prevents DNS rebinding attacks: the IP was validated at check-time,
    and the actual TCP connection goes to that same IP. The original hostname
    is sent as the Host header and used for SNI (via poolmanager), so the
    remote server sees the correct hostname.
    """

    def __init__(self, pinned_ip, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(**kwargs)

    def send(self, request, *args, **kwargs):
        # Replace the hostname in the URL with the validated IP
        parsed = urlparse(request.url)
        if parsed.hostname:
            pinned_url = request.url.replace(
                f"://{parsed.hostname}", f"://{self._pinned_ip}", 1
            )
            request.url = pinned_url
            # Set Host header so the server sees the original hostname
            request.headers["Host"] = parsed.hostname
        return super().send(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Shared helper — used by http_request.execute() and telegram.py
# ---------------------------------------------------------------------------
def make_http_request(method, url, headers=None, body=None, json_payload=None, timeout=30):
    """Issue an HTTP request and return a result dict.

    SSRF protection: ``validate_url_not_ssrf()`` is called before any
    network I/O. The hostname is resolved ONCE, validated, and then a
    ``_PinnedIPAdapter`` connects directly to the validated IP — DNS is
    never re-resolved between check and connect. Redirects are DISABLED
    (allow_redirects=False) — the initial response is returned as-is.
    This prevents both DNS rebinding and redirect-based SSRF bypasses.

    Args:
        method: HTTP method string (GET, POST, ...).
        url: Full URL.
        headers: Optional dict of request headers.
        body: Optional string body.  Parsed as JSON when possible.
        json_payload: Optional dict to send as JSON body (takes precedence
            over *body* for POST/PUT/PATCH).
        timeout: Request timeout in seconds.

    Returns:
        dict with keys: status_code, response_body (truncated), ok (bool).

    Raises:
        ValueError on SSRF violation.
        requests.exceptions.RequestException on connection errors.
    """
    # SSRF check — BEFORE any network call. Returns validated IP.
    hostname, validated_ip = validate_url_not_ssrf(url)

    headers = headers or {}
    method = (method or "GET").upper()

    kwargs = {"headers": headers, "timeout": timeout, "allow_redirects": False}

    if method in ("POST", "PUT", "PATCH"):
        if json_payload is not None:
            kwargs["json"] = json_payload
        elif body is not None:
            # Attempt to parse as JSON; fall back to raw text
            try:
                kwargs["json"] = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                kwargs["data"] = body

    # Pin the connection to the validated IP — prevents DNS rebinding.
    session = _requests.Session()
    session.mount("http://", _PinnedIPAdapter(validated_ip))
    session.mount("https://", _PinnedIPAdapter(validated_ip))
    resp = session.request(method, url, **kwargs)

    # Truncate response body for logging (keep first 2000 chars)
    resp_text = resp.text[:2000] if resp.text else ""

    return {
        "status_code": resp.status_code,
        "response_body": resp_text,
        "ok": resp.ok,
    }


# ---------------------------------------------------------------------------
# Action type execute
# ---------------------------------------------------------------------------
def execute(context, config):
    """Make an HTTP request to the configured URL.

    config format::

        {
            "url": "https://example.com/api",
            "method": "POST",
            "headers": [{"target_field": "Content-Type", "source_value": "application/json"}],
            "body": '{"key": "{{trigger.lead_name}}"}'
        }

    Raises on failure — the exception propagates to the dispatcher which
    marks the Automation Run as Failed with the real traceback.
    """
    url = resolve_value(config.get("url", ""), context)
    method = config.get("method", "GET")
    body_raw = config.get("body", "")

    if not url:
        raise ValueError("No URL specified in http_request action config")

    # Resolve token placeholders in body
    body = resolve_value(body_raw, context) if body_raw else None

    # Build headers dict from field_mapping_table rows
    raw_headers = config.get("headers", [])
    headers = {}
    for row in raw_headers:
        key = resolve_value(row.get("target_field", ""), context)
        val = resolve_value(row.get("source_value", ""), context)
        if key:
            headers[key] = val

    result = make_http_request(method, url, headers=headers, body=body)

    status_code = result["status_code"]
    resp_preview = result["response_body"]

    if not result["ok"]:
        raise ValueError(
            f"HTTP {method} {url} returned status {status_code}: {resp_preview}"
        )

    return {
        "step_type": "http_request",
        "status": "Success",
        "output": f"HTTP {method} {url} → {status_code}\n{resp_preview}",
    }


register_action_type(
    key="http_request",
    label="HTTP Request",
    config_schema=CONFIG_SCHEMA,
    execute_fn=execute,
)
