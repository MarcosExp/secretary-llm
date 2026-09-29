from datetime import date

import pytest

from app import clock
from app.sdk.modules import load_modules
from app.sdk.registry import Registry


@pytest.fixture
def call(conn):
    registry = Registry(load_modules(), conn)

    def run(tool, **arguments):
        with conn:
            return registry.call("jobs", tool, arguments)
    return run


def test_application_lifecycle(call, monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: date(2027, 1, 5))
    app = call("add_application", company="Acme Robotics", role="SWE", deadline="2027-01-31")
    assert app.stage == "researching"

    updated = call("update_application", company="acme", stage="applied", next_step="Wait for OA")
    assert (updated.stage, updated.applied_on, updated.next_step) == ("applied", "2027-01-05", "Wait for OA")

    result = call("add_application_task", company="Acme", title="Prepare coding test", due="2027-01-20")
    assert result["application_id"] == app.id
    detail = call("get_application", application_id=app.id)
    assert [t["title"] for t in detail["open_tasks"]] == ["Prepare coding test"]


def test_list_hides_closed_applications_and_sorts_by_date(call):
    call("add_application", company="Later", deadline="2027-03-01")
    call("add_application", company="Sooner", deadline="2027-02-01")
    call("add_application", company="Undated")
    closed = call("add_application", company="Closed")
    call("update_application", application_id=closed.id, stage="rejected")
    assert [a["company"] for a in call("list_applications")] == ["Sooner", "Later", "Undated"]
    assert [a["company"] for a in call("list_applications", stages=["rejected"])] == ["Closed"]


def test_ambiguous_company_lists_candidates(call):
    call("add_application", company="Acme", role="SWE")
    call("add_application", company="Acme", role="Data")
    with pytest.raises(ValueError, match="matches several applications"):
        call("update_application", company="acme", stage="applied")


def test_lists_return_short_summaries(call, conn):
    call("add_application", company="Acme", link="https://example.com/job", notes="long " * 100,
         next_step="x" * 200)
    [summary] = call("list_applications")
    assert set(summary) == {"id", "company", "role", "stage", "priority", "deadline", "follow_up_by", "next_step"}
    assert len(summary["next_step"]) == 80 and summary["next_step"].endswith("…")
    full = call("get_application", application_id=summary["id"])["application"]
    assert full.link == "https://example.com/job" and full.next_step == "x" * 200

    registry = Registry(load_modules(), conn)
    with conn:
        task = registry.call("core", "add_task", {"title": "T", "notes": "n" * 300})
        [listed] = registry.call("core", "list_tasks", {})
        full_task = registry.call("core", "get_task", {"task_id": task.id})
    assert "created_at" not in listed and len(listed["notes"]) == 80
    assert full_task.notes == "n" * 300
