"""Her own-accord lines nobody answered: what the pet and her messages both look at before trying again."""

from __future__ import annotations

from typing import Any

from nest.core.desk import unanswered


def row(external_id: str, at: float, *, hers: bool = True) -> dict[str, Any]:
    return {"external_id": external_id, "sent_at": at, "from_self": hers}


def test_after_the_users_last_word_newest_first() -> None:
    rows = [
        row("app:c:1", 10),
        row("app:u", 20, hers=False),
        row("app:c:2", 30),
        row("app:r:3", 35),  # a reminder of hers: passed over
        row("app:o:4", 40),
    ]
    assert unanswered(rows, since=0) == ["o:", "c:"]
    assert unanswered(rows, since=32) == ["o:"]  # yesterday's do not count today
    assert unanswered([*rows, row("x", 50, hers=False)], since=0) == []
    assert unanswered([], since=0) == []
