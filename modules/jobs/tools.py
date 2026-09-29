from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from app.sdk import ModuleContext, tool
from modules.jobs import repo

Stage = Literal["researching", "to_apply", "applied", "assessment", "interview", "final",
                "offer", "rejected", "no_response", "archived"]
Priority = Literal["dream", "strong", "backup"]
Visa = Literal["sponsors", "eu_only", "unclear", "no_sponsorship"]
Company = Annotated[str, Field(description="Company name; a unique partial name is enough")]


def _id(ctx: ModuleContext, application_id: int | None, company: str | None) -> int:
    if application_id is not None:
        return application_id
    if company:
        return repo.application_id(ctx.db, company)
    raise ValueError("give application_id or company")


@tool("""
    List job applications as short summaries, soonest deadline or follow-up first. By
    default only open ones (not rejected, no response or archived). Use get_application
    for the full record (link, contact, visa, notes…).
""")
def list_applications(ctx: ModuleContext, stages: list[Stage] | None = None) -> list[dict]:
    return [repo.summary(a) for a in repo.list_applications(ctx.db, tuple(stages) if stages else None)]


@tool("Show one application with its open tasks.")
def get_application(ctx: ModuleContext, application_id: int | None = None,
                    company: Company | None = None) -> dict:
    app_id = _id(ctx, application_id, company)
    return {"application": repo.get(ctx.db, app_id), "open_tasks": repo.open_tasks(ctx.db, app_id)}


@tool("Track a new application or company of interest.")
def add_application(
    ctx: ModuleContext,
    company: str,
    role: str | None = None,
    program: str | None = None,
    stage: Stage = "researching",
    priority: Priority | None = None,
    location: str | None = None,
    link: str | None = None,
    deadline: date | None = None,
    next_step: str | None = None,
    notes: str | None = None,
) -> repo.Application:
    return repo.add(ctx.db, company, role=role, program=program, stage=stage, priority=priority,
                    location=location, link=link, deadline=deadline, next_step=next_step, notes=notes)


@tool("""
    Update an application: stage, next step, dates or details. Only the fields given change.
    Moving to "applied" without applied_on sets it to today.
""")
def update_application(
    ctx: ModuleContext,
    application_id: int | None = None,
    company: Company | None = None,
    stage: Stage | None = None,
    priority: Priority | None = None,
    next_step: str | None = None,
    applied_on: date | None = None,
    deadline: date | None = None,
    follow_up_by: date | None = None,
    visa: Visa | None = None,
    referral: bool | None = None,
    contact: str | None = None,
    link: str | None = None,
    notes: str | None = None,
) -> repo.Application:
    app_id = _id(ctx, application_id, company)
    fields = {k: v for k, v in dict(
        stage=stage, priority=priority, next_step=next_step, applied_on=applied_on,
        deadline=deadline, follow_up_by=follow_up_by, visa=visa, referral=referral,
        contact=contact, link=link, notes=notes,
    ).items() if v is not None}
    if stage == "applied" and applied_on is None and repo.get(ctx.db, app_id).applied_on is None:
        fields["applied_on"] = ctx.today()
    return repo.update(ctx.db, app_id, **fields)


@tool("Create a task for an application (prepare an interview, send a follow-up…) and link it.")
def add_application_task(
    ctx: ModuleContext,
    title: str,
    application_id: int | None = None,
    company: Company | None = None,
    due: date | None = None,
    priority: Literal["P1", "P2", "P3"] = "P2",
    estimate_h: Annotated[float | None, Field(gt=0)] = None,
) -> dict:
    app_id = _id(ctx, application_id, company)
    task = ctx.call("core.add_task", title=title, due=due, priority=priority, estimate_h=estimate_h)
    repo.link_task(ctx.db, app_id, task.id)
    return {"application_id": app_id, "task": task}
