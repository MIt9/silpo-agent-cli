"""MCP Client / Auth: OAuth2.1+PKCE browser login against the Silpo MCP
server, OS keyring token storage, refresh-on-expiry, and a single
`call(tool, args)` wrapper used by every other module for all silpo_* tool
calls.

Live schema note: the MCP server's tools/list response (order objects,
delivery-address objects) is not publicly documented and requires an
authenticated call. See ../../docs/mcp_schema.md for what has and hasn't
been verified against the live server.
"""

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import keyring

SERVER_URL = "https://mcp.silpo.ua/mcp"
AUTHORIZE_URL = "https://mcp.silpo.ua/authorize"
TOKEN_URL = "https://mcp.silpo.ua/token"
REGISTER_URL = "https://mcp.silpo.ua/register"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"

KEYRING_SERVICE = "silpo-agent"
KEYRING_USERNAME = "mcp-token"


class MCPError(Exception):
    pass


class AuthError(Exception):
    pass


class TokenStore:
    """Wraps the OS keyring (via the `keyring` package) as JSON-serialized
    token storage. This is the mockable system boundary for auth persistence.
    """

    def __init__(self, service: str = KEYRING_SERVICE, username: str = KEYRING_USERNAME):
        self.service = service
        self.username = username

    def load(self) -> dict | None:
        raw = keyring.get_password(self.service, self.username)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def save(self, token: dict) -> None:
        keyring.set_password(self.service, self.username, json.dumps(token))


def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise AuthError(f"{url} returned {exc.code}: {exc.read().decode(errors='replace')}") from exc


def _post_form(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise AuthError(f"{url} returned {exc.code}: {exc.read().decode(errors='replace')}") from exc


def _token_from_response(resp: dict) -> dict:
    if "access_token" not in resp:
        raise AuthError(f"token endpoint response missing access_token: {resp}")
    return {
        "access_token": resp["access_token"],
        "refresh_token": resp.get("refresh_token"),
        "expires_at": time.time() + float(resp.get("expires_in", 3600)),
    }


def _register_client() -> str:
    resp = _post_json(
        REGISTER_URL,
        {
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "silpo-agent-cli",
        },
    )
    if "client_id" not in resp:
        raise AuthError(f"dynamic client registration response missing client_id: {resp}")
    return resp["client_id"]


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.auth_code = params.get("code", [None])[0]
        self.server.auth_error = params.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Login complete, you can close this tab.")

    def log_message(self, *args):
        pass


def _wait_for_redirect() -> str:
    httpd = HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    httpd.auth_code = None
    httpd.auth_error = None
    httpd.handle_request()
    if httpd.auth_error or not httpd.auth_code:
        raise AuthError(f"OAuth authorize redirect returned an error: {httpd.auth_error}")
    return httpd.auth_code


def pkce_browser_login() -> dict:
    """Full OAuth2.1+PKCE flow: dynamic client registration, browser
    authorize, local redirect capture, code-for-token exchange.
    """
    client_id = _register_client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    authorize_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    webbrowser.open(authorize_url)
    code = _wait_for_redirect()

    resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    return _token_from_response(resp)


def refresh_token_http(refresh_token: str) -> dict:
    resp = _post_form(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": refresh_token})
    return _token_from_response(resp)


def call_tool_http(server_url: str, tool: str, args: dict, access_token: str) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    resp = _post_json(server_url, payload, headers={"Authorization": f"Bearer {access_token}"})
    if "error" in resp:
        raise MCPError(resp["error"])
    return resp.get("result", resp)


class MCPClient:
    """Single entrypoint for all silpo_* tool calls: `client.call(tool, args)`.
    Handles token load/refresh/first-login transparently.
    """

    def __init__(
        self,
        server_url: str = SERVER_URL,
        token_store: TokenStore | None = None,
        call_tool_http=call_tool_http,
        login=pkce_browser_login,
        refresh=refresh_token_http,
        now=time.time,
    ):
        self.server_url = server_url
        self.token_store = token_store or TokenStore()
        self.call_tool_http = call_tool_http
        self.login = login
        self.refresh = refresh
        self.now = now

    def call(self, tool: str, args: dict | None = None) -> dict:
        access_token = self._ensure_token()
        return self.call_tool_http(self.server_url, tool, args or {}, access_token)

    def _ensure_token(self) -> str:
        token = self.token_store.load()
        if token is None:
            token = self.login()
            self.token_store.save(token)
        elif token["expires_at"] <= self.now():
            token = self.refresh(token["refresh_token"])
            self.token_store.save(token)
        return token["access_token"]
