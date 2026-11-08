"""OneBot v11 payloads → platform-neutral events. Pure functions; raise ValueError on malformed input."""

from __future__ import annotations

import time
from typing import Any

from nest.core.events import ChannelRef, Event, MessageEvent, RecallEvent, Segment
from nest.core.segments import at_target, render_text


def parse_segments(message: Any) -> tuple[Segment, ...]:
    if isinstance(message, str):  # string format (CQ codes); only expected if the endpoint is misconfigured
        return (Segment("text", {"text": message}),) if message else ()
    if not isinstance(message, list):
        raise ValueError(f"消息内容不是数组：{type(message).__name__}")
    segments = []
    for item in message:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ValueError("消息段格式不对")
        data = item.get("data")
        data = data if isinstance(data, dict) else {}
        if item["type"] == "at" and "qq" in data:  # the neutral form: see nest.core.segments
            data = {"id": str(data["qq"]), **{k: v for k, v in data.items() if k != "qq"}}
        segments.append(Segment(item["type"], data))
    return tuple(segments)


def outbound(segment: Segment) -> dict[str, Any]:
    """One segment as OneBot wants it: an @ names its target "qq"."""
    if segment.type != "at":
        return {"type": segment.type, "data": dict(segment.data)}
    data = {key: value for key, value in segment.data.items() if key != "id"}
    return {"type": "at", "data": {"qq": at_target(segment.data), **data}}


def _channel(payload: dict[str, Any], account: str, *, self_sent: bool) -> ChannelRef:
    kind = payload.get("message_type")
    if kind == "group":
        return ChannelRef("qq", account, "group", str(payload["group_id"]))
    if kind == "private":
        # For our own private messages the peer is the target, not the sender.
        peer = payload.get("target_id") if self_sent and payload.get("target_id") else payload["user_id"]
        return ChannelRef("qq", account, "private", str(peer))
    raise ValueError(f"未知的消息类型：{kind}")


def normalize(payload: dict[str, Any], account: str, *, backfilled: bool = False) -> Event | None:
    """Translate one OneBot event. Returns None for events nest does not use (meta, requests…)."""
    post_type = payload.get("post_type")
    if post_type in ("message", "message_sent"):
        if payload.get("message_type") not in ("group", "private"):
            return None
        sender_id = str(payload["user_id"])
        from_self = post_type == "message_sent" or sender_id == account
        segments = parse_segments(payload.get("message"))
        sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
        reply = next((s.data.get("id") for s in segments if s.type == "reply"), None)
        return MessageEvent(
            channel=_channel(payload, account, self_sent=from_self),
            message_id=str(payload["message_id"]),
            sender_id=sender_id,
            sender_name=str(sender.get("card") or sender.get("nickname") or sender_id),
            segments=segments,
            text=render_text(segments),
            timestamp=float(payload.get("time") or time.time()),
            from_self=from_self,
            mentions=frozenset(str(s.data["id"]) for s in segments if s.type == "at" and "id" in s.data),
            reply_to=str(reply) if reply is not None else None,
            backfilled=backfilled,
        )
    if post_type == "notice" and payload.get("notice_type") in ("group_recall", "friend_recall"):
        if payload["notice_type"] == "group_recall":
            channel = ChannelRef("qq", account, "group", str(payload["group_id"]))
        else:
            channel = ChannelRef("qq", account, "private", str(payload["user_id"]))
        return RecallEvent(channel, str(payload["message_id"]), float(payload.get("time") or time.time()))
    return None
