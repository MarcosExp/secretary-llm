import textwrap

import pytest

from app.db.retention import purge_archived
from app.importers.notion import NotionClient, NotionImportError, load_mapping, plain, run

MAPPING = """
notion:
  courses:
    database_id: courses-db
    properties: {title: Course, program: Program, status: Status, exam_date: Exam date, ects: ECTS}
    values:
      status: {"Enrolled": enrolled, "Planned": "archived | Planned"}
  study_units:
    database_id: units-db
    properties: {title: Topic, course: Course, position: Unit, status: Status, confidence: Confidence, estimate_h: Estimate (h)}
    values:
      status: {"Studying": in_progress, "Reviewed": "in_progress | Reviewed"}
      confidence: {"Shaky": 2, "Solid": 5}
  applications:
    database_id: apps-db
    properties: {title: Company, stage: Stage, referral: Referral, process_open: Open, deadline: Deadline}
    values:
      stage: {"Applied": applied, "Coding test": assessment}
      process_open: {"Yes": true, "No": false, "Closed for me": "false | Closed for me"}
  tasks:
    database_id: tasks-db
    properties: {title: Task, status: Status, priority: Priority, area: Area, due: Due, estimate_h: Estimate (h), course: Course, application: Application}
    values:
      status: {"Not started": todo, "Blocked": "todo | Blocked", "Done": done}
      priority: {"High": P1, "Low": P3}
"""


def title(text):
    return {"type": "title", "title": [{"plain_text": text}]}


def select(name):
    return {"type": "select", "select": {"name": name} if name else None}


def date_(value):
    return {"type": "date", "date": {"start": value} if value else None}


def number(value):
    return {"type": "number", "number": value}


def relation(*ids):
    return {"type": "relation", "relation": [{"id": i} for i in ids]}


def page(page_id, **properties):
    return {"id": page_id, "properties": properties}


PAGES = {
    "courses-db": [
        page("c-1111", Course=title("Algebra"), Program=select("BSc"), Status=select("Enrolled"),
             **{"Exam date": date_("2027-01-20"), "ECTS": number(6)}),
        page("c-2222", Course=title("Networks"), Program=select("BSc"), Status=select("Planned")),
    ],
    "units-db": [
        page("u-1", Topic=title("Matrices"), Course=relation("c-1111"), Unit=number(1),
             Status=select("Reviewed"), Confidence=select("Solid"), **{"Estimate (h)": number(8)}),
        page("u-2", Topic=title("Orphan"), Course=relation(), Status=select("Studying")),
    ],
    "apps-db": [
        page("a-1", Company=title("Acme"), Stage=select("Coding test"),
             Referral={"type": "checkbox", "checkbox": True}, Open=select("Closed for me"),
             Deadline=date_("2027-02-01T09:00:00.000+01:00")),
    ],
    "tasks-db": [
        page("t-1", Task=title("Exercise sheet 3"), Status=select("Blocked"), Priority=select("High"),
             Area=select("Degree"), Due=date_("2027-01-10"), Course=relation("c-1111"),
             **{"Estimate (h)": number(2)}),
        page("t-2", Task=title("Prepare Acme test"), Status=select("Done"), Priority=select("Low"),
             Application=relation("a-1")),
        page("t-3", Task=title("Weird status"), Status=select("Someday maybe")),
    ],
}


class FakeNotion:
    def __init__(self, pages):
        self.data = pages

    def pages(self, database_id):
        yield from self.data[database_id]


@pytest.fixture
def mappings(tmp_path):
    path = tmp_path / "notion.yaml"
    path.write_text(textwrap.dedent(MAPPING), encoding="utf-8")
    return load_mapping(path)


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


def test_full_import_with_relations_and_notes(conn, mappings):
    report = run(conn, FakeNotion(PAGES), mappings)
    assert dict(report.created) == {"courses": 2, "study_units": 1, "applications": 1, "tasks": 3}

    algebra = one(conn, "SELECT * FROM studies_courses WHERE name = 'Algebra'")
    assert (algebra["status"], algebra["exam_date"], algebra["ects"]) == ("enrolled", "2027-01-20", 6)
    assert algebra["external_ref"] == "notion:c1111"
    assert tuple(one(conn, "SELECT status, notes FROM studies_courses WHERE name = 'Networks'")) == ("archived", "Planned")

    unit = one(conn, "SELECT * FROM studies_units")
    assert (unit["course_id"], unit["position"], unit["status"], unit["confidence"], unit["notes"]) == (
        algebra["id"], 1, "in_progress", 5, "Reviewed")

    app = one(conn, "SELECT * FROM jobs_applications")
    assert (app["stage"], app["referral"], app["process_open"], app["deadline"], app["notes"]) == (
        "assessment", 1, 0, "2027-02-01", "Closed for me")

    blocked = one(conn, "SELECT t.*, a.name AS area FROM core_tasks t JOIN core_areas a ON a.id = t.area_id "
                        "WHERE title = 'Exercise sheet 3'")
    assert (blocked["status"], blocked["priority"], blocked["notes"], blocked["area"]) == ("todo", "P1", "Blocked", "Degree")
    assert one(conn, "SELECT course_id FROM studies_course_tasks WHERE task_id = ?", blocked["id"])[0] == algebra["id"]

    done = one(conn, "SELECT * FROM core_tasks WHERE title = 'Prepare Acme test'")
    assert done["status"] == "done" and done["completed_at"] is not None
    assert one(conn, "SELECT application_id FROM jobs_application_tasks WHERE task_id = ?", done["id"])[0] == app["id"]


def test_problems_become_warnings_not_failures(conn, mappings):
    report = run(conn, FakeNotion(PAGES), mappings)
    assert any("'Orphan' has no imported course" in w for w in report.warnings)
    assert any("no mapping for status 'Someday maybe'" in w for w in report.warnings)
    weird = one(conn, "SELECT status, notes FROM core_tasks WHERE title = 'Weird status'")
    assert tuple(weird) == ("todo", "status: Someday maybe")  # default used, original kept in notes


def test_running_again_only_adds_new_pages(conn, mappings):
    run(conn, FakeNotion(PAGES), mappings)
    conn.execute("UPDATE core_tasks SET title = 'Edited here' WHERE title = 'Exercise sheet 3'")
    conn.commit()
    pages = {**PAGES, "tasks-db": PAGES["tasks-db"] + [page("t-4", Task=title("New in Notion"))]}
    report = run(conn, FakeNotion(pages), mappings)
    assert report.created["tasks"] == 1 and report.skipped["tasks"] == 3
    assert one(conn, "SELECT COUNT(*) FROM core_tasks WHERE title = 'Edited here'")[0] == 1  # not overwritten


def test_dry_run_saves_nothing(conn, mappings):
    report = run(conn, FakeNotion(PAGES), mappings, dry_run=True)
    assert report.created["tasks"] == 3
    assert one(conn, "SELECT COUNT(*) FROM core_tasks")[0] == 0
    assert one(conn, "SELECT COUNT(*) FROM studies_courses")[0] == 0


def test_missing_mapping_file(tmp_path):
    with pytest.raises(NotionImportError, match="notion.example.yaml"):
        load_mapping(tmp_path / "notion.yaml")


def test_property_values():
    assert plain(title("  Hi ")) == "Hi"
    assert plain({"type": "rich_text", "rich_text": []}) is None
    assert plain(select(None)) is None
    assert plain(date_("2027-01-10T08:00:00.000Z")) == "2027-01-10"
    assert plain(relation("AB-cd-12")) == ["abcd12"]
    assert plain({"type": "multi_select", "multi_select": [{"name": "x"}, {"name": "y"}]}) == ["x", "y"]
    assert plain({"type": "formula", "formula": {}}) is None


class Response:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class Session:
    def __init__(self, *responses):
        self.headers, self.responses, self.bodies = {}, list(responses), []

    def post(self, url, json=None, timeout=None):
        self.bodies.append(json)
        return self.responses.pop(0)


def test_client_pages_and_skips_trashed():
    session = Session(
        Response(200, {"results": [{"id": "1"}, {"id": "2", "in_trash": True}], "has_more": True, "next_cursor": "c2"}),
        Response(200, {"results": [{"id": "3", "archived": True}, {"id": "4"}], "has_more": False}),
    )
    client = NotionClient("secret", session=session)
    assert [p["id"] for p in client.pages("db")] == ["1", "4"]
    assert session.bodies[1]["start_cursor"] == "c2"
    assert session.headers["Authorization"] == "Bearer secret"


def test_client_explains_permission_errors():
    client = NotionClient("secret", session=Session(Response(404)))
    with pytest.raises(NotionImportError, match="shared with the integration"):
        list(client.pages("db"))


def test_rows_deleted_after_30_days_archived_are_not_imported_again(conn, mappings):
    run(conn, FakeNotion(PAGES), mappings)
    # "Networks" came in archived; pretend that was long ago.
    conn.execute("UPDATE studies_courses SET archived_at = '2000-01-01' WHERE name = 'Networks'")
    conn.commit()
    assert purge_archived(conn) == {"studies_courses": 1}
    report = run(conn, FakeNotion(PAGES), mappings)
    assert sum(report.created.values()) == 0 and report.skipped["courses"] == 2
    assert one(conn, "SELECT COUNT(*) FROM studies_courses WHERE name = 'Networks'")[0] == 0
