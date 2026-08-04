from silpo_agent.cli import main


def test_no_args_prints_help_and_exits_zero(capsys):
    assert main([]) == 0
    assert "silpo-agent" in capsys.readouterr().out


def test_reorder_command_runs_as_noop(capsys):
    assert main(["reorder"]) == 0
    assert "reorder" in capsys.readouterr().out
