import time

from silpo_agent.auth import MCPClient, TokenStore


class FakeTokenStore:
    def __init__(self, token=None):
        self._token = token

    def load(self):
        return self._token

    def save(self, token):
        self._token = token


def fail_login():
    raise AssertionError("login should not be called when a valid token is stored")


def fail_refresh(refresh_token, client_id=None):
    raise AssertionError("refresh should not be called when token is still valid")


def test_warm_start_reuses_stored_token_without_login_or_refresh():
    token_store = FakeTokenStore(
        {"access_token": "warm-token", "refresh_token": "r1", "expires_at": time.time() + 3600}
    )
    calls = []

    def fake_call_tool_http(server_url, tool, args, access_token):
        calls.append((server_url, tool, args, access_token))
        return {"ok": True}

    client = MCPClient(
        server_url="https://mcp.silpo.ua/mcp",
        token_store=token_store,
        call_tool_http=fake_call_tool_http,
        login=fail_login,
        refresh=fail_refresh,
        now=time.time,
    )

    result = client.call("silpo_get_my_online_orders", {"last": 5})

    assert result == {"ok": True}
    assert calls == [
        ("https://mcp.silpo.ua/mcp", "silpo_get_my_online_orders", {"last": 5}, "warm-token")
    ]


def test_expired_token_triggers_refresh_before_call():
    expired = {"access_token": "old-token", "refresh_token": "r1", "expires_at": 1000.0}
    token_store = FakeTokenStore(expired)
    refreshed = {"access_token": "new-token", "refresh_token": "r2", "expires_at": 9999999999.0}

    def fake_refresh(refresh_token, client_id=None):
        assert refresh_token == "r1"
        return refreshed

    used_tokens = []

    def fake_call_tool_http(server_url, tool, args, access_token):
        used_tokens.append(access_token)
        return {"ok": True}

    client = MCPClient(
        token_store=token_store,
        call_tool_http=fake_call_tool_http,
        login=fail_login,
        refresh=fake_refresh,
        now=lambda: 2000.0,
    )

    client.call("silpo_get_my_shopping_cart", {})

    assert used_tokens == ["new-token"]
    assert token_store.load() == refreshed


def test_first_run_with_no_stored_token_triggers_browser_login():
    token_store = FakeTokenStore(None)
    fresh_token = {"access_token": "first-token", "refresh_token": "r1", "expires_at": 9999999999.0}

    def fake_login():
        return fresh_token

    used_tokens = []

    def fake_call_tool_http(server_url, tool, args, access_token):
        used_tokens.append(access_token)
        return {"ok": True}

    client = MCPClient(
        token_store=token_store,
        call_tool_http=fake_call_tool_http,
        login=fake_login,
        refresh=fail_refresh,
        now=time.time,
    )

    client.call("silpo_get_my_delivery_addresses", {})

    assert used_tokens == ["first-token"]
    assert token_store.load() == fresh_token


def test_token_store_round_trips_through_keyring(monkeypatch, tmp_path):
    saved = {}

    monkeypatch.setattr(
        "silpo_agent.auth.keyring.get_password", lambda s, u: saved.get((s, u))
    )
    monkeypatch.setattr(
        "silpo_agent.auth.keyring.set_password",
        lambda s, u, v: saved.__setitem__((s, u), v),
    )

    store = TokenStore(service="test-service", username="test-user", file_path=str(tmp_path / "token.json"))
    assert store.load() is None

    store.save({"access_token": "abc", "refresh_token": "r", "expires_at": 123.0})

    assert store.load() == {"access_token": "abc", "refresh_token": "r", "expires_at": 123.0}


def test_token_store_load_returns_none_for_corrupt_value(monkeypatch, tmp_path):
    monkeypatch.setattr("silpo_agent.auth.keyring.get_password", lambda s, u: "not-json")

    store = TokenStore(service="test-service", username="test-user", file_path=str(tmp_path / "token.json"))

    assert store.load() is None


def test_expired_token_refresh_carries_stored_client_id():
    expired = {"access_token": "old", "refresh_token": "r1", "expires_at": 1000.0, "client_id": "cid-1"}
    token_store = FakeTokenStore(expired)
    seen = {}

    def fake_refresh(refresh_token, client_id=None):
        seen["refresh_token"] = refresh_token
        seen["client_id"] = client_id
        return {"access_token": "new", "refresh_token": "r2", "expires_at": 9999999999.0, "client_id": "cid-1"}

    client = MCPClient(
        token_store=token_store,
        call_tool_http=lambda *a: {"ok": True},
        login=fail_login,
        refresh=fake_refresh,
        now=lambda: 2000.0,
    )

    client.call("silpo_get_my_shopping_cart", {})

    assert seen == {"refresh_token": "r1", "client_id": "cid-1"}


def test_rejected_refresh_falls_back_to_browser_login():
    """A stored token from before client_id was persisted (or a revoked
    grant) makes the endpoint answer 401 -- recover with a fresh login
    instead of surfacing AuthError as a traceback."""
    from silpo_agent.auth import AuthError

    expired = {"access_token": "old", "refresh_token": "r1", "expires_at": 1000.0}
    token_store = FakeTokenStore(expired)
    fresh = {"access_token": "fresh", "refresh_token": "r2", "expires_at": 9999999999.0, "client_id": "cid-9"}

    def failing_refresh(refresh_token, client_id=None):
        raise AuthError("https://mcp.silpo.ua/token returned 401: invalid_client")

    used = []

    client = MCPClient(
        token_store=token_store,
        call_tool_http=lambda s, t, a, tok: used.append(tok) or {"ok": True},
        login=lambda: fresh,
        refresh=failing_refresh,
        now=lambda: 2000.0,
    )

    client.call("silpo_get_my_shopping_cart", {})

    assert used == ["fresh"]
    assert token_store.load() == fresh


def test_expired_token_without_refresh_token_logs_in_directly():
    token_store = FakeTokenStore({"access_token": "old", "expires_at": 1000.0})
    fresh = {"access_token": "fresh", "refresh_token": "r2", "expires_at": 9999999999.0, "client_id": "cid-9"}

    client = MCPClient(
        token_store=token_store,
        call_tool_http=lambda *a: {"ok": True},
        login=lambda: fresh,
        refresh=fail_refresh,
        now=lambda: 2000.0,
    )

    client.call("silpo_get_my_shopping_cart", {})

    assert token_store.load() == fresh


def test_refresh_token_http_sends_client_id_when_known(monkeypatch):
    import silpo_agent.auth as auth_module

    captured = {}

    def fake_post_form(url, fields):
        captured.update(fields)
        return {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}

    monkeypatch.setattr(auth_module, "_post_form", fake_post_form)

    token = auth_module.refresh_token_http("r1", "cid-1")

    assert captured == {"grant_type": "refresh_token", "refresh_token": "r1", "client_id": "cid-1"}
    assert token["client_id"] == "cid-1"


def test_refresh_token_http_omits_absent_client_id(monkeypatch):
    import silpo_agent.auth as auth_module

    captured = {}

    def fake_post_form(url, fields):
        captured.update(fields)
        return {"access_token": "new", "expires_in": 3600}

    monkeypatch.setattr(auth_module, "_post_form", fake_post_form)

    auth_module.refresh_token_http("r1")

    assert "client_id" not in captured


def test_bind_falls_back_to_free_port_when_default_is_taken():
    import socket

    import silpo_agent.auth as auth_module

    squatter = None
    try:
        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", auth_module.REDIRECT_PORT))
        squatter.listen(1)
    except OSError:
        pass

    try:
        httpd, redirect_uri = auth_module._bind_callback_server()
    finally:
        if squatter is not None:
            squatter.close()

    assert redirect_uri != auth_module.REDIRECT_URI
    assert redirect_uri.startswith("http://localhost:")
    assert redirect_uri.endswith("/callback")
    port = int(redirect_uri.split(":")[2].split("/")[0])
    assert port != auth_module.REDIRECT_PORT
    probe = socket.create_connection(("127.0.0.1", port), timeout=5)
    probe.close()
    httpd.server_close()


def test_wait_for_redirect_returns_code_and_closes_port():
    import http.client
    import threading

    import silpo_agent.auth as auth_module

    httpd, redirect_uri = auth_module._bind_callback_server()
    port = httpd.server_address[1]

    def hit_callback():
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/callback?code=abc123&state=s1")
        conn.getresponse().read()
        conn.close()

    thread = threading.Thread(target=hit_callback)
    thread.start()
    code = auth_module._wait_for_redirect(httpd, "s1")
    thread.join(timeout=10)

    assert code == "abc123"
    import socket

    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        assert False, "callback port should be closed after the redirect"
    except OSError:
        pass


def test_wait_for_redirect_rejects_state_mismatch():
    import http.client
    import threading

    import pytest

    import silpo_agent.auth as auth_module

    httpd, _ = auth_module._bind_callback_server()
    port = httpd.server_address[1]

    def hit_callback():
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/callback?code=abc123&state=wrong")
        conn.getresponse().read()
        conn.close()

    thread = threading.Thread(target=hit_callback)
    thread.start()
    try:
        with pytest.raises(auth_module.AuthError):
            auth_module._wait_for_redirect(httpd, "s1")
    finally:
        thread.join(timeout=10)


def test_register_client_sends_bound_redirect_uri(monkeypatch):
    import silpo_agent.auth as auth_module

    captured = {}

    def fake_post_json(url, payload, headers=None):
        captured.update(payload)
        return {"client_id": "cid-1"}

    monkeypatch.setattr(auth_module, "_post_json", fake_post_json)

    assert auth_module._register_client("http://localhost:54321/callback") == "cid-1"
    assert captured["redirect_uris"] == ["http://localhost:54321/callback"]


def test_token_store_falls_back_to_file_when_keyring_fails(monkeypatch, tmp_path):
    import keyring.errors

    import silpo_agent.auth as auth_module

    def boom(*a, **k):
        raise keyring.errors.PasswordSetError("locked")

    monkeypatch.setattr(auth_module.keyring, "get_password", boom)
    monkeypatch.setattr(auth_module.keyring, "set_password", boom)

    store = auth_module.TokenStore(
        service="test-service", username="test-user", file_path=str(tmp_path / "token.json")
    )
    token = {"access_token": "abc", "refresh_token": "r", "expires_at": 123.0, "client_id": "cid-1"}

    store.save(token)

    assert store.load() == token
    assert (tmp_path / "token.json").stat().st_mode & 0o777 == 0o600


def test_token_store_prefers_keyring_over_stale_file(monkeypatch, tmp_path):
    import silpo_agent.auth as auth_module

    file_path = tmp_path / "token.json"
    file_path.write_text('{"access_token": "stale"}')
    monkeypatch.setattr(
        auth_module.keyring, "get_password", lambda s, u: '{"access_token": "fresh"}'
    )

    store = auth_module.TokenStore(
        service="test-service", username="test-user", file_path=str(file_path)
    )

    assert store.load() == {"access_token": "fresh"}


def test_token_store_clear_wipes_keyring_and_file(monkeypatch, tmp_path):
    import silpo_agent.auth as auth_module

    deleted = []
    file_path = tmp_path / "token.json"
    file_path.write_text("{}")
    monkeypatch.setattr(
        auth_module.keyring, "delete_password", lambda s, u: deleted.append((s, u))
    )

    auth_module.TokenStore(
        service="test-service", username="test-user", file_path=str(file_path)
    ).clear()

    assert deleted == [("test-service", "test-user")]
    assert not file_path.exists()


def test_repeated_calls_share_one_login_when_store_loses_writes():
    """Regression for the live double-login: the store can accept a save yet
    return nothing on the next load (keychain write/read asymmetry), so the
    client must reuse the in-process token instead of logging in per call."""
    import silpo_agent.auth as auth_module

    logins = []

    class AmnesiacStore:
        def load(self):
            return None

        def save(self, token):
            pass

        def clear(self):
            pass

    def counting_login():
        logins.append(True)
        return {"access_token": "t", "refresh_token": "r", "expires_at": 9999999999.0}

    client = auth_module.MCPClient(
        token_store=AmnesiacStore(),
        call_tool_http=lambda *a: {"ok": True},
        login=counting_login,
        refresh=fail_refresh,
        now=lambda: 2000.0,
    )

    client.call("silpo_get_my_shopping_cart", {})
    client.call("silpo_get_shopping_cart_by_id", {})

    assert len(logins) == 1


def test_save_mirrors_to_both_backends(monkeypatch, tmp_path):
    import json

    import silpo_agent.auth as auth_module

    saved = {}
    monkeypatch.setattr(
        auth_module.keyring, "set_password", lambda s, u, v: saved.__setitem__((s, u), v)
    )
    monkeypatch.setattr(auth_module.keyring, "get_password", lambda s, u: None)

    file_path = tmp_path / "token.json"
    store = auth_module.TokenStore(
        service="test-service", username="test-user", file_path=str(file_path)
    )
    token = {"access_token": "abc", "refresh_token": "r", "expires_at": 123.0}

    store.save(token)

    assert json.loads(saved[("test-service", "test-user")]) == token
    assert json.loads(file_path.read_text()) == token


def test_load_takes_freshest_across_backends(monkeypatch, tmp_path):
    import silpo_agent.auth as auth_module

    file_path = tmp_path / "token.json"
    file_path.write_text('{"access_token": "file-fresh", "expires_at": 9999.0}')
    monkeypatch.setattr(
        auth_module.keyring,
        "get_password",
        lambda s, u: '{"access_token": "chain-stale", "expires_at": 1000.0}',
    )

    store = auth_module.TokenStore(
        service="test-service", username="test-user", file_path=str(file_path)
    )
    assert store.load()["access_token"] == "file-fresh"

    file_path.write_text('{"access_token": "file-stale", "expires_at": 500.0}')
    monkeypatch.setattr(
        auth_module.keyring,
        "get_password",
        lambda s, u: '{"access_token": "chain-fresh", "expires_at": 9999.0}',
    )
    assert store.load()["access_token"] == "chain-fresh"
import json

import silpo_agent.auth as auth_module
from silpo_agent.auth import MCPError, call_tool_http


def test_call_tool_http_unwraps_mcp_content_text_envelope(monkeypatch):
    inner = {"success": True, "addresses": [{"id": "a1"}]}

    def fake_post_json(url, payload, headers=None):
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "silpo_get_my_delivery_addresses"
        return {"result": {"content": [{"type": "text", "text": json.dumps(inner)}]}}

    monkeypatch.setattr(auth_module, "_post_json", fake_post_json)

    result = call_tool_http("https://mcp.silpo.ua/mcp", "silpo_get_my_delivery_addresses", {}, "token")

    assert result == inner


def test_call_tool_http_mcp_level_error_raises_mcp_error(monkeypatch):
    def fake_post_json(url, payload, headers=None):
        return {"result": {"content": [{"type": "text", "text": "boom"}], "isError": True}}

    monkeypatch.setattr(auth_module, "_post_json", fake_post_json)

    try:
        call_tool_http("https://mcp.silpo.ua/mcp", "silpo_get_my_delivery_addresses", {}, "token")
        assert False, "expected MCPError"
    except MCPError:
        pass


def test_call_tool_http_passes_through_non_content_result(monkeypatch):
    def fake_post_json(url, payload, headers=None):
        return {"result": {"tools": []}}

    monkeypatch.setattr(auth_module, "_post_json", fake_post_json)

    result = call_tool_http("https://mcp.silpo.ua/mcp", "tools/list", {}, "token")

    assert result == {"tools": []}
