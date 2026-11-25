"""示例伙伴: companions that come with nest to try it with. Nobody is there until the user takes one in.

Each is a companion pack (nest.core.companion_pack) as a folder in nest/samples: plugin.json, companion/ (her
persona and picture), and the pets and stickers that go with her. Taken in, she is a companion like any the
user made: nothing of her is tied to the sample any more.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from nest.core.companion_pack import Pack, PackProblem, read_pack
from nest.core.plugins import Manifest, read_manifest

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent / "samples"


@dataclass(frozen=True)
class Sample:
    id: str  # its plugin id; a companion made from it has her pets in "<id>.for…"
    manifest: Manifest
    pack: Pack
    folder: Path

    def zipped(self) -> bytes:
        """The pack as the zip an import reads."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:  # pictures: nothing left to compress
            for path in sorted(self.folder.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(self.folder).as_posix())
        return buffer.getvalue()


def samples(root: Path = ROOT) -> list[Sample]:
    """What comes with nest, in the order of the folders' names. One that cannot be read is left out."""
    found = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if not (folder / "plugin.json").is_file():
            continue
        manifest, problems = read_manifest(folder)
        try:
            if manifest is None:
                raise PackProblem("；".join(problems))
            pack = read_pack(folder / str(manifest.provides.get("companion") or "companion"))
        except PackProblem as exc:
            log.warning("示例伙伴 %s 用不了：%s", folder.name, exc)
            continue
        found.append(Sample(manifest.id, manifest, pack, folder))
    return found
