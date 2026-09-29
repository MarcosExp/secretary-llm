from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from app.sdk import ModuleContext, tool
from modules.core import repo
from modules.core.repo import UNSET, Task

Priority = Literal["P1", "P2", "P3"]
Status = Literal["todo", "doing", "done", "archived"]
Hours = Annotated[float, Field(gt=0, description="Estimated hours")]


@tool("List the active areas tasks can belong to.")
def list_areas(ctx: ModuleContext) -> list[str]:
    return repo.list_areas(ctx.db)


@tool("Create a new area. Only when the user explicitly asks for a new area.")
def add_area(ctx: ModuleContext, name: str) -> dict:
    return {"id": repo.add_area(ctx.db, name), "name": name.strip()}


@tool("""
    List tasks ordered by priority, then due date (undated last).
    By default only open tasks (todo, doing) are returned.
""")
def list_tasks(
    ctx: ModuleContext,
    statuses: Annotated[list[Status] | None, Field(description="Defaults to todo and doing")] = None,
    area: str | None = None,
    due_before: Annotated[date | None, Field(description="Only tasks due on or before this date")] = None,
) -> list[Task]:
    return repo.list_tasks(
        ctx.db, statuses=tuple(statuses or repo.OPEN_STATUSES), area=area, due_before=due_before
    )


@tool("Create a task.")
def add_task(
    ctx: ModuleContext,
    title: str,
    priority: Priority = "P2",
    area: Annotated[str | None, Field(description="Name of an existing area")] = None,
    due: date | None = None,
    estimate_h: Hours | None = None,
    notes: str | None = None,
) -> Task:
    return repo.add_task(
        ctx.db, title, priority=priority, area=area, due=due, estimate_h=estimate_h, notes=notes
    )


@tool("""
    Change fields of a task. Only the fields given are changed.
    To remove an optional field, list it in `clear`. Status can move between todo and
    doing (or reopen a done task); use complete_task or archive_task to close one.
""")
def update_task(
    ctx: ModuleContext,
    task_id: int,
    title: str | None = None,
    priority: Priority | None = None,
    area: str | None = None,
    due: date | None = None,
    estimate_h: Hours | None = None,
    notes: str | None = None,
    status: Literal["todo", "doing"] | None = None,
    clear: list[Literal["area", "due", "estimate_h", "notes"]] = [],
) -> Task:
    def value(name, given):
        if name in clear:
            return None
        return UNSET if given is None else given

    return repo.update_task(
        ctx.db,
        task_id,
        title=UNSET if title is None else title,
        priority=UNSET if priority is None else priority,
        area=value("area", area),
        due=value("due", due),
        estimate_h=value("estimate_h", estimate_h),
        notes=value("notes", notes),
        status=UNSET if status is None else status,
    )


@tool("Mark a task as done.")
def complete_task(ctx: ModuleContext, task_id: int) -> Task:
    return repo.complete_task(ctx.db, task_id)


@tool("Archive a task (tasks are never deleted). Use it when a task is dropped or cancelled.")
def archive_task(ctx: ModuleContext, task_id: int) -> Task:
    return repo.archive_task(ctx.db, task_id)
