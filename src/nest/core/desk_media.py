"""桌面聊天的图片和语音: a picture you send her, her voice messages, what she read aloud.

Files in data/desk-media, named by a random id and their type (`<32 hex>.<ext>`); a message points at one
with a segment whose url is `desk-media:<name>`, and the console fetches it from /api/desk/media/<name>.
The type is read from the first bytes, never from what the sender claims. Files go when they are older
than the messages kept (nest.core.hub.Hub.purge).
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from nest.storage.avatars import image_type

SCHEME = "desk-media:"
MAX_PICTURE = 10 * 1024 * 1024
MAX_PICTURES = 9  # in one message
FILE_EXT = "bin"
NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp|mp3|wav|ogg|webm|m4a|bin)$")
PICTURE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "m4a": "audio/mp4",
}


def audio_ext(data: bytes) -> str | None:
    """The kind of audio from its first bytes, or None."""
    if data.startswith(b"ID3") or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if data[4:8] == b"ftyp":
        return "m4a"
    return None


def picture_url(name: str) -> str:
    return f"{SCHEME}{name}"


def name_of(url: str) -> str | None:
    """The file name a `desk-media:` url points at, when it is one of ours."""
    if not url.startswith(SCHEME):
        return None
    name = url[len(SCHEME) :]
    return name if NAME.match(name) else None


class DeskMedia:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def save(self, data: bytes, ext: str) -> str:
        """Store the bytes; their file name."""
        name = f"{uuid.uuid4().hex}.{ext}"
        if not NAME.match(name):
            raise ValueError(f"不认识的类型：{ext}")
        self.folder.mkdir(parents=True, exist_ok=True)
        part = self.folder / f"{name}.part"
        part.write_bytes(data)
        os.replace(part, self.folder / name)
        return name

    def save_picture(self, data: bytes) -> str | None:
        """A picture's file name, or None when it is not a PNG, JPEG, GIF or WebP."""
        kind = image_type(data)
        return self.save(data, PICTURE_EXT[kind]) if kind else None

    def save_audio(self, data: bytes) -> str | None:
        ext = audio_ext(data)
        return self.save(data, ext) if ext else None

    def path(self, name: str) -> Path | None:
        """The file, when the name is one of ours and it is still there."""
        if not NAME.match(name):
            return None
        found = self.folder / name
        return found if found.is_file() else None

    def save_file(self, data: bytes) -> str:
        """Any file, kept only until the message it goes with is sent (nest.core.desk_files); never shown."""
        return self.save(data, FILE_EXT)

    def file(self, name: str) -> bool:
        return name.endswith(f".{FILE_EXT}") and self.path(name) is not None

    def remove(self, name: str) -> None:
        found = self.path(name)
        if found is not None:
            found.unlink(missing_ok=True)

    def picture(self, name: str) -> bool:
        """Whether the name is a picture that is still there."""
        return name.rpartition(".")[2] in PICTURE_EXT.values() and self.path(name) is not None

    def read(self, name: str) -> bytes | None:
        found = self.path(name)
        return found.read_bytes() if found else None

    def purge(self, before: float) -> int:
        """Delete the files older than `before`; how many went."""
        if not self.folder.is_dir():
            return 0
        gone = 0
        for item in self.folder.iterdir():
            try:
                if item.is_file() and item.stat().st_mtime < before:
                    item.unlink()
                    gone += 1
            except OSError:
                continue
        return gone


def content_type(name: str) -> str:
    return CONTENT_TYPES.get(name.rsplit(".", 1)[-1], "application/octet-stream")
