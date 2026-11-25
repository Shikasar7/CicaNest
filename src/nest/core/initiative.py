"""主动性的设置: how often they come to the user on their own and when they
leave them alone, which (cheaper) model writes the small talk and the day plans, and what nest has noticed of
the user's own hours.

作息: the desktop app says about every minute whether the user is at the computer; nest keeps, per day, how
many minutes of each hour they were (four weeks, on this machine only), and from that says when they usually
come and go. Used for 「这么晚还不睡」 (late for them, not midnight for everyone) and as a line in what
she knows when she comes to them.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, replace
from statistics import median
from typing import Any

from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

SETTING = "initiative"
RHYTHM = "rhythm"  # setting: {"days": {"2026-11-25": [minutes at the computer in each hour]}}
# name: (default, lowest, highest)
NUMBERS = {
    "quiet_from": (23, 0, 23),
    "quiet_until": (8, 0, 23),
    "chatter_per_day": (10, 0, 50),
    "chatter_per_hour": (4, 0, 20),
    "outreach_per_bot": (3, 0, 20),
    "outreach_daily": (6, 0, 50),
    "outreach_hourly": (2, 0, 10),
}
PROVIDERS = ("chatter_provider", "life_provider")
DAY_STARTS = 5  # a day of the user's begins at 5: what they do after midnight belongs to the day before
KEEP_DAYS = 28
NEED_DAYS = 4  # of a kind (weekday, weekend) before anything is said of it
HERE_MINUTES = 5.0  # in an hour, to count as at the computer in it


@dataclass(frozen=True)
class Usual:
    """When the user usually comes to the computer and goes, in hours after DAY_STARTS o'clock."""

    start: float
    end: float

    @staticmethod
    def clock(hours: float) -> str:
        return f"{int(hours + DAY_STARTS) % 24} 点"

    @property
    def said(self) -> str:
        return f"对方平时大约 {self.clock(self.start)}开电脑，{self.clock(self.end)}离开。"


def day_of(now: float) -> dt.date:
    """The user's day `now` belongs to."""
    return (dt.datetime.fromtimestamp(now) - dt.timedelta(hours=DAY_STARTS)).date()


def into_day(now: float) -> float:
    """Hours since the user's day began."""
    moment = dt.datetime.fromtimestamp(now)
    return (moment.hour + moment.minute / 60 - DAY_STARTS) % 24


class Rhythm:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.days: dict[str, list[float]] = {}
        self._changed = False

    async def load(self) -> None:
        stored = (await SettingsRepo(self.db).all()).get(RHYTHM)
        days = stored.get("days") if isinstance(stored, dict) else None
        self.days = {
            str(day): [float(m) for m in hours]
            for day, hours in (days.items() if isinstance(days, dict) else [])
            if isinstance(hours, list) and len(hours) == 24
        }

    def note(self, now: float, minutes: float) -> None:
        """The user was at the computer for the last `minutes`."""
        hours = self.days.setdefault(day_of(now).isoformat(), [0.0] * 24)
        hour = dt.datetime.fromtimestamp(now).hour
        hours[hour] = min(60.0, hours[hour] + minutes)
        self._changed = True

    async def save(self) -> None:
        if not self._changed:
            return
        self._changed = False
        self.days = dict(sorted(self.days.items())[-KEEP_DAYS:])
        shown = {day: [round(m) for m in hours] for day, hours in self.days.items()}
        await SettingsRepo(self.db).set_many({RHYTHM: {"days": shown}})

    async def forget(self) -> None:
        self.days = {}
        self._changed = True
        await self.save()

    def usual(self, now: float) -> Usual | None:
        """Their usual hours on days like this one (weekdays, or the weekend); None when not known yet."""
        today = day_of(now)
        starts, ends = [], []
        for day, hours in self.days.items():
            date = dt.date.fromisoformat(day)
            if date == today or (date.weekday() >= 5) != (today.weekday() >= 5):
                continue
            here = [(hour - DAY_STARTS) % 24 for hour in range(24) if hours[hour] >= HERE_MINUTES]
            if here:
                starts.append(min(here))
                ends.append(max(here) + 1)
        if len(starts) < NEED_DAYS:
            return None
        return Usual(median(starts), median(ends))

    def line(self, now: float) -> str:
        usual = self.usual(now)
        return usual.said if usual is not None else ""


class InitiativeKeeper:
    """`apply`: called with the settings whenever they change (the app hands them to whom they concern)."""

    def __init__(self, db: Database, apply: Callable[[dict[str, Any]], None]) -> None:
        self.db = db
        self.rhythm = Rhythm(db)
        self._apply = apply
        self.values: dict[str, Any] = {}

    async def start(self) -> None:
        await self.rhythm.load()
        stored = (await SettingsRepo(self.db).all()).get(SETTING)
        self._take(stored if isinstance(stored, dict) else {})

    def _take(self, stored: dict[str, Any]) -> None:
        found: dict[str, Any] = {}
        for name, (default, low, high) in NUMBERS.items():
            value = stored.get(name, default)
            usable = isinstance(value, int | float) and not isinstance(value, bool)
            found[name] = min(high, max(low, int(value))) if usable else default
        for name in PROVIDERS:
            value = stored.get(name)
            found[name] = value if isinstance(value, str) and value else None
        found["rhythm"] = stored.get("rhythm", True) is True
        self.values = found
        self._apply(found)

    async def save(self, changes: dict[str, Any]) -> None:
        self._take({**self.values, **changes})
        await SettingsRepo(self.db).set_many({SETTING: self.values})
        if not self.values["rhythm"]:
            await self.rhythm.forget()

    def view(self, now: float) -> dict[str, Any]:
        usual = {}
        for name, shift in (("weekday", 0), ("weekend", 5)):
            # Any day of that kind: this week's Monday or Saturday.
            date = day_of(now)
            at = now + (shift - date.weekday()) * 86400
            found = self.rhythm.usual(at)
            usual[name] = found.said.replace("对方", "你") if found is not None else ""
        return {
            "values": self.values,
            "ranges": {k: {"default": d, "min": low, "max": high} for k, (d, low, high) in NUMBERS.items()},
            "rhythm_days": len(self.rhythm.days),
            "usual": usual,
        }


def quiet_now(hour: int, start: int, until: int) -> bool:
    """Whether an hour is in the quiet ones (the same hour twice: there are none)."""
    if start == until:
        return False
    return hour >= start or hour < until if start > until else start <= hour < until


def apply_to(values: dict[str, Any], chatter: Any, outreach: Any, brain: Any) -> None:
    if chatter is not None:
        chatter.config = replace(
            chatter.config,
            per_day=values["chatter_per_day"],
            per_hour=values["chatter_per_hour"],
            quiet_from=values["quiet_from"],
            quiet_until=values["quiet_until"],
        )
    if outreach is not None:
        outreach.config = replace(
            outreach.config,
            per_bot_daily=values["outreach_per_bot"],
            daily=values["outreach_daily"],
            hourly=values["outreach_hourly"],
            quiet_from=values["quiet_from"],
            quiet_until=values["quiet_until"],
        )
        outreach.learns = values["rhythm"]
    brain.small = {
        "chatter": values["chatter_provider"],
        "outreach": values["chatter_provider"],
        "life": values["life_provider"],
    }
