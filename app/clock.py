"""The user's local date and time.

SQLite timestamps (created_at, ...) stay in UTC. Dates the user talks about
("today", "Friday", due dates) are in SECRETARY_TIMEZONE.
"""

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo


def timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("SECRETARY_TIMEZONE", "UTC"))


def now() -> datetime:
    return datetime.now(timezone())


def today() -> date:
    return now().date()
