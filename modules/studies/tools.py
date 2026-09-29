from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from app.sdk import ModuleContext, tool
from modules.studies import repo

Course = Annotated[str, Field(description="Course name; a unique partial name is enough")]
Hours = Annotated[float, Field(gt=0, description="Hours")]
Confidence = Annotated[int, Field(ge=1, le=5, description="How well the user knows it, 1-5")]


@tool("List courses with program and exam date, soonest exam first.")
def list_courses(ctx: ModuleContext, include_finished: bool = False) -> list[dict]:
    return repo.list_courses(ctx.db, include_finished)


@tool("""
    Study progress per enrolled course: units with estimated and done hours, status and
    confidence, totals, and days left to the exam.
""")
def study_progress(ctx: ModuleContext, course: Course | None = None) -> list[repo.CourseProgress]:
    course_id = repo.course_id(ctx.db, course) if course else None
    return repo.progress(ctx.db, ctx.today(), course_id)


@tool("""
    Record hours studied. Without a unit name, the hours go to the course's single
    unit in progress. Optionally update the user's confidence in that unit.
""")
def log_study_hours(
    ctx: ModuleContext,
    course: Course,
    hours: Hours,
    unit: str | None = None,
    confidence: Confidence | None = None,
) -> dict:
    course_id = repo.course_id(ctx.db, course)
    unit_id = repo.unit_id(ctx.db, course_id, unit)
    repo.log_hours(ctx.db, unit_id, hours, confidence)
    return {"logged_h": hours, "progress": repo.progress(ctx.db, ctx.today(), course_id)}


@tool("Mark a study unit as done (or back in progress).")
def set_unit_status(
    ctx: ModuleContext,
    course: Course,
    unit: str,
    status: Literal["pending", "in_progress", "done", "archived"],
) -> dict:
    unit_id = repo.unit_id(ctx.db, repo.course_id(ctx.db, course), unit)
    repo.set_unit_status(ctx.db, unit_id, status)
    return {"unit_id": unit_id, "status": status}


@tool("Add a course the user is enrolled in. The program is created if it does not exist.")
def add_course(
    ctx: ModuleContext,
    name: str,
    program: str | None = None,
    exam_date: date | None = None,
) -> dict:
    return {"id": repo.add_course(ctx.db, name, program, exam_date), "name": name.strip()}


@tool("Set or change a course's exam date, or mark the course as passed or archived.")
def update_course(
    ctx: ModuleContext,
    course: Course,
    exam_date: date | None = None,
    status: Literal["enrolled", "passed", "archived"] | None = None,
) -> dict:
    course_id = repo.course_id(ctx.db, course)
    if exam_date:
        repo.set_exam_date(ctx.db, course_id, exam_date)
    if status:
        repo.set_course_status(ctx.db, course_id, status)
    return {"id": course_id, "exam_date": exam_date, "status": status}


@tool("Add a study unit (topic) to a course, with its estimated hours and planned dates.")
def add_unit(
    ctx: ModuleContext,
    course: Course,
    unit: str,
    estimate_h: Hours | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    course_id = repo.course_id(ctx.db, course)
    return {"id": repo.add_unit(ctx.db, course_id, unit, estimate_h, start, end), "unit": unit}


@tool("""
    Create a task for a course (an assignment, an exercise sheet, ...) and link it to the
    course. The task is created by the core module.
""")
def add_course_task(
    ctx: ModuleContext,
    course: Course,
    title: str,
    due: date | None = None,
    priority: Literal["P1", "P2", "P3"] = "P2",
    estimate_h: Hours | None = None,
    area: Annotated[str | None, Field(description="Existing core area, if the user uses one for studies")] = None,
) -> dict:
    course_id = repo.course_id(ctx.db, course)
    task = ctx.call("core.add_task", title=title, due=due, priority=priority,
                    estimate_h=estimate_h, area=area)
    repo.link_task(ctx.db, course_id, task.id)
    return {"course_id": course_id, "task": task}


@tool("List the open tasks linked to a course (include_closed adds done and archived ones).")
def list_course_tasks(ctx: ModuleContext, course: Course, include_closed: bool = False) -> list[dict]:
    return repo.course_tasks(ctx.db, repo.course_id(ctx.db, course), include_closed)
