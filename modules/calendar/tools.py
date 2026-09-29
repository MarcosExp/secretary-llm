"""Calendar tools. Times are local to the user (SECRETARY_TIMEZONE) unless they carry an offset.

Events are never deleted, events with other attendees are never moved, and no
invitations are sent.
"""

from datetime import date, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.sdk import ModuleContext, tool
from modules.calendar.client import CalendarClient, Event, day_range, describe, local, span

LocalTime = Annotated[datetime, Field(description="Local date and time, e.g. 2027-01-15T18:00")]
MAX_EVENT = timedelta(hours=24)


class NewEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    start: LocalTime
    end: LocalTime | None = None
    duration_min: Annotated[int | None, Field(gt=0, le=1440, description="Used when end is not given")] = None
    description: str | None = None


class Move(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    new_start: LocalTime
    new_end: Annotated[datetime | None, Field(description="Defaults to keeping the event's duration")] = None


def _calendar(ctx: ModuleContext) -> CalendarClient:
    return ctx.service("calendar")


def _times(start: datetime, end: datetime | None, duration_min: int | None) -> tuple[datetime, datetime]:
    start = local(start)
    if end is not None:
        end = local(end)
    elif duration_min is not None:
        end = start + timedelta(minutes=duration_min)
    else:
        raise ValueError("give either end or duration_min")
    if not start < end <= start + MAX_EVENT:
        raise ValueError(f"invalid time range {span(start, end)} (end must be after start, max 24h)")
    return start, end


def _move_target(calendar: CalendarClient, move: Move) -> tuple[Event, datetime, datetime]:
    event = calendar.get_event(move.event_id)
    if event.all_day:
        raise ValueError(f"{describe(event)} is an all-day event; move it in Google Calendar")
    if event.has_other_attendees:
        raise ValueError(f"{describe(event)} has other attendees; change it in Google Calendar")
    start = local(move.new_start)
    end = local(move.new_end) if move.new_end else start + (event.end - event.start)
    _times(start, end, None)
    return event, start, end


@tool("List calendar events between two dates (inclusive). Use it before creating or moving events.")
def calendar_list_events(ctx: ModuleContext, start: date, end: date | None = None) -> list[Event]:
    return _calendar(ctx).list_events(*day_range(start, end or start))


def _summarize_create(ctx: ModuleContext, events: list[NewEvent]) -> str:
    lines = [f"Create '{e.title}' ({span(*_times(e.start, e.end, e.duration_min))})" for e in events]
    return "\n".join(lines)


@tool(
    """
    Create calendar events. A single event is created right away; several events are
    proposed to the user and created only after they confirm.
    """,
    confirm=lambda args: len(args["events"]) > 1,
    summarize=_summarize_create,
)
def calendar_create_events(ctx: ModuleContext, events: Annotated[list[NewEvent], Field(min_length=1, max_length=50)]) -> list[Event]:
    calendar = _calendar(ctx)
    planned = [(e, *_times(e.start, e.end, e.duration_min)) for e in events]  # validate all first
    return [calendar.create_event(e.title, start, end, e.description) for e, start, end in planned]


def _summarize_moves(ctx: ModuleContext, moves: list[Move]) -> str:
    calendar = _calendar(ctx)
    lines = []
    for move in moves:
        event, start, end = _move_target(calendar, move)
        lines.append(f"Move {describe(event)} to {span(start, end)}")
    return "\n".join(lines)


@tool(
    """
    Move calendar events to new times (find their IDs with calendar_list_events).
    Always proposed to the user first; the events move only after they confirm.
    """,
    confirm=True,
    summarize=_summarize_moves,
)
def calendar_move_events(ctx: ModuleContext, moves: Annotated[list[Move], Field(min_length=1, max_length=50)]) -> list[Event]:
    calendar = _calendar(ctx)
    planned = [_move_target(calendar, move) for move in moves]  # validate all first
    return [calendar.move_event(event.id, start, end) for event, start, end in planned]
