from click.testing import CliRunner
from nivos.cli.eval_cmd import eval_group


def test_nivos_eval_cli_runs_tasks():
    runner = CliRunner()
    result = runner.invoke(eval_group, ["--tasks-file", "evals/tasks.yaml"])
    assert result.exit_code == 0
    assert "Nivos Evaluation Suite — Real Tasks" in result.output
    assert "Pass Rate: 100.0%" in result.output
