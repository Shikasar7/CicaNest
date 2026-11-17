"""生日祝福's tables (from 025): birthdays as profiles show them, and who was wished where, once a year."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from nest.storage.db import Database

CHUNK = 500  # ids per IN (...) query


@dataclass(frozen=True, slots=True)
class Birthday:
    month: int  # 0 with day 0: the profile does not show it
    day: int
    recheck_at: float


@dataclass(frozen=True, slots=True)
class Wish:
    bot_id: str
    outcome: str  # "replied" | "would_reply" | "failed"
    tries: int


class BirthdayStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def known(self, platform: str, user_ids: Sequence[str]) -> dict[str, Birthday]:
        found: dict[str, Birthday] = {}
        for start in range(0, len(user_ids), CHUNK):
            chunk = list(user_ids[start : start + CHUNK])
            marks = ",".join("?" * len(chunk))
            rows = await self.db.fetchall(
                f"""SELECT user_id, month, day, recheck_at FROM birthdays
                    WHERE platform = ? AND user_id IN ({marks})""",
                (platform, *chunk),
            )
            found.update(
                {row["user_id"]: Birthday(row["month"], row["day"], row["recheck_at"]) for row in rows}
            )
        return found

    async def save(
        self, platform: str, user_id: str, month: int, day: int, *, now: float, recheck_at: float
    ) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO birthdays (platform, user_id, month, day, checked_at, recheck_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (platform, user_id) DO UPDATE SET month = excluded.month, day = excluded.day,
                   checked_at = excluded.checked_at, recheck_at = excluded.recheck_at""",
                (platform, user_id, month, day, now, recheck_at),
            )

    async def wish(self, group_key: str, user_id: str, year: int) -> Wish | None:
        row = await self.db.fetchone(
            """SELECT bot_id, outcome, tries FROM birthday_wishes
               WHERE group_key = ? AND user_id = ? AND year = ?""",
            (group_key, user_id, year),
        )
        return Wish(row["bot_id"], row["outcome"], row["tries"]) if row else None

    async def note(
        self, group_key: str, user_id: str, year: int, *, bot_id: str, outcome: str, said: str, now: float
    ) -> None:
        """What became of this year's wish; a failed one counts its tries."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO birthday_wishes (group_key, user_id, year, bot_id, outcome, said, tries, at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT (group_key, user_id, year) DO UPDATE SET bot_id = excluded.bot_id,
                   outcome = excluded.outcome, said = excluded.said, at = excluded.at,
                   tries = CASE WHEN excluded.outcome = 'failed' AND birthday_wishes.outcome = 'failed'
                                THEN birthday_wishes.tries + 1 ELSE 1 END""",
                (group_key, user_id, year, bot_id, outcome, said, now),
            )

    async def said(self, bot_id: str, limit: int) -> list[str]:
        """The bot's latest birthday wishes (the words, without the @), newest first."""
        rows = await self.db.fetchall(
            """SELECT said FROM birthday_wishes
               WHERE bot_id = ? AND outcome IN ('replied', 'would_reply') AND said != ''
               ORDER BY at DESC LIMIT ?""",
            (bot_id, limit),
        )
        return [row["said"] for row in rows]
