from datetime import date

import pytest

from modules.core import repo
from modules.core.repo import NotFound


@pytest.fixture
def areas(conn):
    for name in ("Work", "Studies"):
        repo.add_area(conn, name)


def test_add_and_get_task(conn, areas):
    task = repo.add_task(conn, "  Write report  ", priority="P1", area="work",
                         due=date(2027, 1, 15), estimate_h=2, notes="draft first")
    assert task.title == "Write report"
    assert task.area == "Work"  # area lookup ignores case
    assert task.due == date(2027, 1, 15)
    assert task.status == "todo"
    assert repo.get_task(conn, task.id) == task


def test_unknown_area_lists_the_available_ones(conn, areas):
    with pytest.raises(NotFound, match="available: Studies, Work"):
        repo.add_task(conn, "Something", area="Gym")


@pytest.mark.parametrize("kwargs", [
    {"title": "   "},
    {"title": "x", "priority": "P9"},
    {"title": "x", "estimate_h": 0},
])
def test_invalid_input_is_rejected(conn, kwargs):
    with pytest.raises(ValueError):
        repo.add_task(conn, **kwargs)


def test_list_orders_by_priority_then_due_date(conn, areas):
    later = repo.add_task(conn, "later", priority="P2", due=date(2027, 2, 1))
    undated = repo.add_task(conn, "undated", priority="P2")
    sooner = repo.add_task(conn, "sooner", priority="P2", due=date(2027, 1, 1))
    urgent = repo.add_task(conn, "urgent", priority="P1")
    assert [t.id for t in repo.list_tasks(conn)] == [urgent.id, sooner.id, later.id, undated.id]


def test_list_filters(conn, areas):
    work = repo.add_task(conn, "work", area="Work", due=date(2027, 1, 10))
    repo.add_task(conn, "study", area="Studies", due=date(2027, 1, 20))
    assert [t.id for t in repo.list_tasks(conn, area="Work")] == [work.id]
    assert [t.id for t in repo.list_tasks(conn, due_before=date(2027, 1, 15))] == [work.id]


def test_done_and_archived_tasks_leave_the_default_list(conn):
    done = repo.add_task(conn, "done")
    archived = repo.add_task(conn, "archived")
    open_task = repo.add_task(conn, "open")
    assert repo.complete_task(conn, done.id).completed_at is not None
    repo.archive_task(conn, archived.id)
    assert [t.id for t in repo.list_tasks(conn)] == [open_task.id]
    assert len(repo.list_tasks(conn, statuses=repo.STATUSES)) == 3


def test_update_changes_and_clears_fields(conn, areas):
    task = repo.add_task(conn, "task", area="Work", due=date(2027, 1, 1), notes="n")
    updated = repo.update_task(conn, task.id, title="renamed", priority="P1", due=None,
                               area="Studies", status="doing")
    assert (updated.title, updated.priority, updated.due, updated.area, updated.status) == (
        "renamed", "P1", None, "Studies", "doing"
    )
    assert updated.notes == "n"  # untouched


def test_reopening_a_done_task_clears_completed_at(conn):
    task = repo.complete_task(conn, repo.add_task(conn, "task").id)
    reopened = repo.update_task(conn, task.id, status="todo")
    assert reopened.completed_at is None


def test_update_cannot_close_a_task(conn):
    task = repo.add_task(conn, "task")
    with pytest.raises(ValueError, match="complete_task or archive_task"):
        repo.update_task(conn, task.id, status="done")


def test_archived_task_cannot_be_completed(conn):
    task = repo.archive_task(conn, repo.add_task(conn, "task").id)
    with pytest.raises(ValueError, match="archived"):
        repo.complete_task(conn, task.id)


def test_missing_task(conn):
    with pytest.raises(NotFound):
        repo.get_task(conn, 999)
