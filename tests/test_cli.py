from datetime import date, timedelta

import pytest

from app.cli import main


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRETARY_DB_PATH", str(tmp_path / "secretary.db"))


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_add_list_done_flow(capsys):
    assert run(capsys, "area", "add", "Studies")[0] == 0
    code, out, _ = run(capsys, "task", "add", "Submit assignment", "-p", "P1", "-a", "Studies",
                       "-d", "tomorrow", "-e", "3")
    assert code == 0 and "Added task 1" in out

    _, out, _ = run(capsys, "task", "list")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert "Submit assignment" in out and tomorrow in out and "3h" in out

    run(capsys, "task", "done", "1")
    assert run(capsys, "task", "list")[1].strip() == "No tasks."
    assert "done" in run(capsys, "task", "list", "--all")[1]


def test_update_with_clear_flag(capsys):
    run(capsys, "task", "add", "Task", "-d", "2027-01-15")
    _, out, _ = run(capsys, "task", "update", "1", "--clear-due", "-p", "P3")
    assert "due       -" in out and "priority  P3" in out


def test_errors_are_reported_without_traceback(capsys):
    code, _, err = run(capsys, "task", "add", "Task", "-a", "Nowhere")
    assert code == 1 and err.startswith("error: unknown area")
    code, _, err = run(capsys, "task", "done", "42")
    assert code == 1 and "not found" in err


def test_invalid_date_is_a_usage_error(capsys):
    with pytest.raises(SystemExit):
        main(["task", "add", "Task", "-d", "next friday"])


def test_seed_example_only_into_empty_database(capsys):
    code, out, _ = run(capsys, "db", "seed-example")
    assert code == 0 and "core, studies" in out
    assert "Submit Linear Algebra assignment 2" in run(capsys, "task", "list")[1]
    code, _, err = run(capsys, "db", "seed-example")
    assert code == 1 and "already has tasks" in err
