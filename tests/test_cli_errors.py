from silpo_agent.auth import AuthError, MCPError
from silpo_agent.cli import _format_mcp_error, main


class ExplodingClient:
    def __init__(self, exc):
        self.exc = exc

    def call(self, tool, args=None):
        raise self.exc


def test_auth_error_becomes_clean_exit_1_not_traceback(capsys):
    code = main([], client=ExplodingClient(AuthError("https://mcp.silpo.ua/token returned 401: nope")))

    assert code == 1
    err = capsys.readouterr().err
    assert "authentication failed" in err
    assert "clear-context --yes" in err


def test_mcp_error_dict_payload_prints_message_not_raw_dict(capsys):
    code = main(
        [], client=ExplodingClient(MCPError({"code": -32000, "message": "slot gone stale"}))
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "slot gone stale" in err
    assert "'code'" not in err


def test_format_mcp_error_prefers_message_field():
    assert _format_mcp_error(MCPError({"message": "boom"})) == "boom"
    assert _format_mcp_error(MCPError("plain")) == "plain"


def test_keyboard_interrupt_aborts_cleanly(capsys):
    code = main([], client=ExplodingClient(KeyboardInterrupt()))

    assert code == 1
    assert "Aborted." in capsys.readouterr().out


def test_eof_on_prompt_aborts_cleanly(monkeypatch, capsys):
    def boom(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)

    class CartClient:
        def call(self, tool, args=None):
            if tool == "silpo_get_my_shopping_cart":
                return {"success": True, "shoppingCartId": "cart-1"}
            return {
                "success": True,
                "cart": {"shipments": [], "calculation": {"validations": []}},
                "loyalty": {},
            }

    code = main(["cart", "clear"], client=CartClient())

    assert code == 1
    assert "Aborted." in capsys.readouterr().out


def test_broken_pipe_exits_quietly(monkeypatch):
    import silpo_agent.cli as cli_module

    silenced = []
    monkeypatch.setattr(cli_module, "_silence_broken_pipe", lambda: silenced.append(True))

    code = main([], client=ExplodingClient(BrokenPipeError()))

    assert code == 0
    assert silenced == [True]


def test_keyring_error_becomes_clean_exit_1_not_traceback(capsys):
    import keyring.errors

    code = main([], client=ExplodingClient(keyring.errors.PasswordSetError("locked")))

    assert code == 1
    assert "keychain" in capsys.readouterr().err
