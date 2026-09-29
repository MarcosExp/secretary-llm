"""Google Calendar client for the calendar module.

GoogleCalendar talks to the Google Calendar REST API with a service account that the
user has shared their calendar with ("Make changes to events"). FakeCalendar keeps
events in memory for tests and demos.

Times cross this boundary as timezone-aware datetimes in the user's timezone.
"""

import os
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from itertools import count
from typing import Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app import clock
from app.sdk.errors import NotFound, ToolError

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
API = "https://www.googleapis.com/calendar/v3"
MARKER = "secretary"  # private extended property on events this app created


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: datetime | date
    end: datetime | date
    all_day: bool = False
    location: str | None = None
    created_by_secretary: bool = False
    has_other_attendees: bool = False


class CalendarClient(Protocol):
    def list_events(self, start: datetime, end: datetime) -> list[Event]: ...
    def get_event(self, event_id: str) -> Event: ...
    def create_event(self, title: str, start: datetime, end: datetime,
                     description: str | None = None) -> Event: ...
    def move_event(self, event_id: str, start: datetime, end: datetime) -> Event: ...


def from_env() -> CalendarClient | None:
    """GoogleCalendar when GOOGLE_CALENDAR_ID and GOOGLE_SERVICE_ACCOUNT_FILE are set, else None."""
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
    credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not calendar_id or not credentials:
        return None
    return GoogleCalendar(calendar_id, credentials, clock.timezone())


class GoogleCalendar:
    def __init__(self, calendar_id: str, credentials_file: str, tz: ZoneInfo, session=None):
        self.calendar_id = calendar_id
        self.tz = tz
        self._credentials_file = credentials_file
        self._session = session

    @property
    def session(self):
        if self._session is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            try:
                credentials = service_account.Credentials.from_service_account_file(
                    self._credentials_file, scopes=SCOPES
                )
            except (OSError, ValueError) as exc:
                raise ToolError(
                    f"could not load the Google service account key ({exc}); see docs/OPERATIONS.md"
                ) from None
            self._session = AuthorizedSession(credentials)
        return self._session

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{API}/calendars/{quote(self.calendar_id, safe='')}/events{path}"
        response = self.session.request(method, url, timeout=20, **kwargs)
        if response.status_code == 404:
            raise NotFound("calendar event not found")
        if response.status_code in (401, 403):
            raise ToolError(
                "Google Calendar refused access; check that the calendar is shared with the "
                "service account with permission to make changes to events"
            )
        if response.status_code >= 400:
            raise ToolError(f"Google Calendar error {response.status_code}: {response.text[:200]}")
        return response.json()

    def list_events(self, start, end):
        items, page = [], None
        while True:
            params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                      "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
            if page:
                params["pageToken"] = page
            data = self._request("GET", "", params=params)
            items += [self._event(item) for item in data.get("items", [])
                      if item.get("status") != "cancelled"]
            page = data.get("nextPageToken")
            if not page:
                return items

    def get_event(self, event_id):
        return self._event(self._request("GET", f"/{event_id}"))

    def create_event(self, title, start, end, description=None):
        body = {
            "summary": title,
            "start": self._when(start),
            "end": self._when(end),
            "extendedProperties": {"private": {MARKER: "1"}},
        }
        if description:
            body["description"] = description
        return self._event(self._request("POST", "", json=body, params={"sendUpdates": "none"}))

    def move_event(self, event_id, start, end):
        body = {"start": self._when(start), "end": self._when(end)}
        return self._event(
            self._request("PATCH", f"/{event_id}", json=body, params={"sendUpdates": "none"})
        )

    def _when(self, moment: datetime) -> dict:
        return {"dateTime": moment.isoformat(), "timeZone": self.tz.key}

    def _event(self, item: dict) -> Event:
        def parse(value: dict) -> datetime | date:
            if "dateTime" in value:
                return datetime.fromisoformat(value["dateTime"]).astimezone(self.tz)
            return date.fromisoformat(value["date"])

        attendees = item.get("attendees") or []
        return Event(
            id=item["id"],
            title=item.get("summary") or "(no title)",
            start=parse(item["start"]),
            end=parse(item["end"]),
            all_day="date" in item["start"],
            location=item.get("location"),
            created_by_secretary=(item.get("extendedProperties") or {})
            .get("private", {}).get(MARKER) == "1",
            has_other_attendees=any(not a.get("self") for a in attendees),
        )


class FakeCalendar:
    """In-memory calendar for tests and offline demos."""

    def __init__(self, events: list[Event] | None = None):
        self.events = {e.id: e for e in events or []}
        self._ids = count(1)

    def list_events(self, start, end):
        def bounds(e: Event):
            if e.all_day:
                tz = start.tzinfo
                return datetime.combine(e.start, time(), tz), datetime.combine(e.end, time(), tz)
            return e.start, e.end

        return sorted(
            (e for e in self.events.values() if bounds(e)[0] < end and bounds(e)[1] > start),
            key=lambda e: bounds(e)[0],
        )

    def get_event(self, event_id):
        if event_id not in self.events:
            raise NotFound("calendar event not found")
        return self.events[event_id]

    def create_event(self, title, start, end, description=None):
        event = Event(f"fake{next(self._ids)}", title, start, end, created_by_secretary=True)
        self.events[event.id] = event
        return event

    def move_event(self, event_id, start, end):
        event = replace(self.get_event(event_id), start=start, end=end)
        self.events[event_id] = event
        return event


def local(moment: datetime) -> datetime:
    """Naive datetimes from the model are in the user's timezone; aware ones are converted."""
    tz = clock.timezone()
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)


def day_range(start: date, end: date) -> tuple[datetime, datetime]:
    tz = clock.timezone()
    return datetime.combine(start, time(), tz), datetime.combine(end + timedelta(days=1), time(), tz)


def describe(event: Event) -> str:
    if event.all_day:
        return f"'{event.title}' (all day {event.start:%a %Y-%m-%d})"
    return f"'{event.title}' ({span(event.start, event.end)})"


def span(start: datetime, end: datetime) -> str:
    same_day = start.date() == end.date()
    return f"{start:%a %Y-%m-%d %H:%M}-{end:%H:%M}" if same_day else f"{start:%a %Y-%m-%d %H:%M} to {end:%a %Y-%m-%d %H:%M}"
