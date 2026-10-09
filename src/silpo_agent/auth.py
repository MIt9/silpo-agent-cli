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
import os
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
# "localhost", not the 127.0.0.1 literal -- a real working Claude Code ->
# mcp.silpo.ua authorize request (HAR-captured 2026-08-04) used
# "localhost", ours used the raw IP; that request never hit the Cloudflare
# block this one did, so this may be a distinguishing factor worth testing.
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"

KEYRING_SERVICE = "silpo-agent"
KEYRING_USERNAME = "mcp-token"


class MCPError(Exception):
    pass


class AuthError(Exception):
    pass


class TokenStore:
    """Wraps the OS keyring (via the `keyring` package) as JSON-serialized
    token storage, mirrored to a 0600 file. This is the mockable system
    boundary for auth persistence.

    Live cases (2026-10-09): macOS keychain writes can fail outright
    (`PasswordSetError: -25244`) after a successful browser login, and --
    worse -- writes can succeed while reads are denied for the same binary,
    stranding the token and forcing a second browser login in the same run.
    Every save mirrors to both backends; every load takes the freshest token
    found in either. `clear` wipes both.
    """

    def __init__(
        self,
        service: str = KEYRING_SERVICE,
        username: str = KEYRING_USERNAME,
        file_path: str | None = None,
    ):
        self.service = service
        self.username = username
        self.file_path = file_path or os.path.join(os.path.expanduser("~"), ".silpo-agent", "token.json")

    def load(self) -> dict | None:
        """Freshest token wins across both backends: a save always mirrors
        to both, but either write can fail independently (keychain denied
        while the file lands, or vice versa) -- trusting only one side can
        strand a fresh token where the next load never looks."""
        keychain_token = self._load_keychain()
        file_token = self._load_file()
        candidates = [t for t in (keychain_token, file_token) if t and t.get("access_token")]
        if not candidates:
            return None
        return max(candidates, key=lambda t: t.get("expires_at") or 0)

    def save(self, token: dict) -> None:
        """Mirror to both backends so a later load finds the token no matter
        which side is readable (live case: keychain writes succeed but reads
        are denied for this binary, stranding the token and forcing a second
        browser login in the same run)."""
        keychain_error: Exception | None = None
        try:
            keyring.set_password(self.service, self.username, json.dumps(token))
        except keyring.errors.KeyringError as exc:
            keychain_error = exc
        try:
            self._save_file(json.dumps(token))
        except OSError:
            if keychain_error is not None:
                raise keychain_error
        return None

    def clear(self) -> None:
        try:
            keyring.delete_password(self.service, self.username)
        except keyring.errors.PasswordDeleteError:
            pass
        try:
            os.remove(self.file_path)
        except OSError:
            pass

    def _load_keychain(self) -> dict | None:
        try:
            raw = keyring.get_password(self.service, self.username)
        except keyring.errors.KeyringError:
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _load_file(self) -> dict | None:
        try:
            with open(self.file_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _save_file(self, payload: str) -> None:
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        fd = os.open(self.file_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise


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


def _token_from_response(resp: dict, client_id: str | None = None) -> dict:
    if "access_token" not in resp:
        raise AuthError(f"token endpoint response missing access_token: {resp}")
    return {
        "access_token": resp["access_token"],
        "refresh_token": resp.get("refresh_token"),
        "expires_at": time.time() + float(resp.get("expires_in", 3600)),
        "client_id": client_id,
    }


def _register_client(redirect_uri: str = REDIRECT_URI) -> str:
    resp = _post_json(
        REGISTER_URL,
        {
            "redirect_uris": [redirect_uri],
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
        self.server.auth_state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Login complete, you can close this tab.")

    def log_message(self, *args):
        pass


class _CallbackServer(HTTPServer):
    # A previous login run's socket can linger in TIME_WAIT after an
    # abrupt exit -- rebinding must not fail on that alone.
    allow_reuse_address = True


def _bind_callback_server() -> tuple[HTTPServer, str]:
    """Bind the loopback OAuth callback listener, preferring REDIRECT_PORT
    but falling back to an ephemeral free port when something else already
    listens there. Live case (2026-10-09): an unrelated long-running node
    process permanently LISTENs on :8765, so a fixed-port-only bind turns
    every fresh login into `OSError: Address already in use`. The bound
    URI is registered with the server before the browser opens, so the
    fallback stays a legitimate registered redirect."""
    try:
        return _CallbackServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler), REDIRECT_URI
    except OSError:
        httpd = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
        return httpd, f"http://localhost:{httpd.server_address[1]}/callback"


def _wait_for_redirect(httpd: HTTPServer, expected_state: str) -> str:
    httpd.auth_code = None
    httpd.auth_error = None
    httpd.auth_state = None
    try:
        httpd.handle_request()
    finally:
        httpd.server_close()
    if httpd.auth_error or not httpd.auth_code:
        raise AuthError(f"OAuth authorize redirect returned an error: {httpd.auth_error}")
    if httpd.auth_state != expected_state:
        raise AuthError("OAuth authorize redirect returned a mismatched state (possible CSRF)")
    return httpd.auth_code


def pkce_browser_login() -> dict:
    """Full OAuth2.1+PKCE flow: dynamic client registration, browser
    authorize, local redirect capture, code-for-token exchange.

    Includes `resource` (RFC 8707) and `state` on the authorize request --
    a live comparison against a real, working Claude Code -> mcp.silpo.ua
    OAuth request (2026-08-04) showed both present; this implementation was
    missing them, which may be why mcp.silpo.ua/authorize hard-blocked this
    flow with a Cloudflare 403 (see mcp_auth_cloudflare_block memory note).
    """
    client_id = None
    httpd, redirect_uri = _bind_callback_server()
    try:
        client_id = _register_client(redirect_uri)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)

        authorize_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "resource": SERVER_URL,
            }
        )
        webbrowser.open(authorize_url)
        print(f"Opened browser for Silpo login (callback on {redirect_uri}) -- complete it there, then return here.")
        code = _wait_for_redirect(httpd, state)
    except Exception:
        httpd.server_close()
        raise

    resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    return _token_from_response(resp, client_id)


def refresh_token_http(refresh_token: str, client_id: str | None = None) -> dict:
    # Live failure (2026-10-09): the token endpoint answers refreshes
    # without a client_id with 401 invalid_client ("Client ID is required"),
    # so the dynamically registered id must ride along on every token call,
    # not just the authorize request.
    fields = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    if client_id:
        fields["client_id"] = client_id
    resp = _post_form(TOKEN_URL, fields)
    return _token_from_response(resp, client_id)


def call_tool_http(server_url: str, tool: str, args: dict, access_token: str) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    resp = _post_json(server_url, payload, headers={"Authorization": f"Bearer {access_token}"})
    if "error" in resp:
        raise MCPError(resp["error"])
    result = resp.get("result", resp)
    return _unwrap_tool_result(tool, result)


def _unwrap_tool_result(tool: str, result: dict) -> dict:
    """MCP tools/call responses wrap a tool's actual JSON output as a string
    inside result["content"][0]["text"] -- this parses that envelope so
    every other module can keep working with plain dicts. Non-tool-call
    results (e.g. tools/list) have no "content" list and pass through as-is.
    """
    content = result.get("content") if isinstance(result, dict) else None
    if not content:
        return result
    if result.get("isError"):
        raise MCPError(f"{tool} returned an error: {content}")
    text = content[0].get("text") if content and isinstance(content[0], dict) else None
    if text is None:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return result


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
        self._cached_token: dict | None = None

    def call(self, tool: str, args: dict | None = None) -> dict:
        access_token = self._ensure_token()
        return self.call_tool_http(self.server_url, tool, args or {}, access_token)

    def _ensure_token(self) -> str:
        # Process-lifetime cache: a command makes many tool calls, and the
        # store can lie between them (live case: keychain writes succeed but
        # reads are denied, so a second load right after save finds nothing
        # and triggers a second browser login in the same run).
        if (
            self._cached_token is not None
            and self._cached_token.get("access_token")
            and self._cached_token.get("expires_at", 0) > self.now()
        ):
            return self._cached_token["access_token"]
        token = self.token_store.load()
        if token is not None and token.get("access_token") and token.get("expires_at", 0) > self.now():
            self._cached_token = token
            return token["access_token"]
        if token is not None and token.get("refresh_token"):
            try:
                token = self.refresh(token["refresh_token"], token.get("client_id"))
                self.token_store.save(token)
                self._cached_token = token
                return token["access_token"]
            except AuthError:
                pass
        # No usable token (first run, missing refresh token, or the
        # refresh itself was rejected -- e.g. a stored token from before
        # client_id was persisted, or a revoked grant): full browser login
        # instead of surfacing the endpoint's 401 as a traceback.
        token = self.login()
        self.token_store.save(token)
        self._cached_token = token
        return token["access_token"]
