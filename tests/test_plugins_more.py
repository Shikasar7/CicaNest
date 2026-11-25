"""插件: the nest a plugin needs, sticker packs as plugins, an author's signature, newer versions, where to
report; and 表情包: locked pictures, everyone's missing ones."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from nest import __version__
from nest.cli import main
from nest.core import ed25519, market
from nest.core.plugins import newer, read_manifest
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401 (fixture)
from tests.test_market import INDEX_URL as INDEX
from tests.test_market import no_dns  # noqa: F401 (fixture: made-up hosts are not looked up)
from tests.test_plugins import package
from tests.test_sticker_packs import AVATAR, drawn_pack, new_pack, painter
from tests.test_stickers import picture

# -- signatures -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seed", "public", "message", "signature"),
    [  # RFC 8032, 7.1: tests 1, 2 and 3
        (
            "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
            "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
            "",
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46b"
            "d25bf5f0595bbe24655141438e7a100b",
        ),
        (
            "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
            "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
            "72",
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c"
            "387b2eaeb4302aeeb00d291612bb0c00",
        ),
        (
            "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
            "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
            "af82",
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc659"
            "4a7c15e9716ed28dc027beceea1ec40a",
        ),
    ],
)
def test_ed25519_as_in_the_rfc(seed: str, public: str, message: str, signature: str) -> None:
    key, said, made = bytes.fromhex(seed), bytes.fromhex(message), bytes.fromhex(signature)
    assert ed25519.public_key(key).hex() == public
    assert ed25519.sign(key, said) == made
    assert ed25519.verify(bytes.fromhex(public), said, made)
    assert not ed25519.verify(bytes.fromhex(public), said + b"x", made)
    assert not ed25519.verify(bytes.fromhex(public), said, made[:32] + bytes(32))
    assert not ed25519.verify(b"short", said, made)


def test_an_author_makes_a_key_and_signs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key, zipped = tmp_path / "my.key", tmp_path / "p.zip"
    assert main(["plugin-key", str(key)]) == 0
    assert main(["plugin-key", str(key)]) == 1  # never overwritten
    zipped.write_bytes(package({"id": "cica.hello", "version": "1.2.0", "name": "你好"}))
    capsys.readouterr()
    assert main(["plugin-sign", str(zipped), "--key", str(key)]) == 0
    said = json.loads("{" + capsys.readouterr().out + "}")
    sha = hashlib.sha256(zipped.read_bytes()).hexdigest()
    entry = {"id": "cica.hello", "version": "1.2.0", "sha256": sha, **said}
    assert market.signed_by(entry) == said["author_key"]
    with pytest.raises(market.MarketError, match="对不上"):
        market.signed_by(entry | {"version": "1.2.1"})
    assert market.signature_state(entry, "") == "signed"
    assert market.signature_state(entry, "someone else's") == "changed"
    assert market.signature_state(entry | {"signature": "AA=="}, "") == "bad"
    unsigned = entry | {"author_key": "", "signature": ""}
    assert market.signature_state(unsigned, "") == ""
    assert market.signature_state(unsigned, said["author_key"]) == "changed"
    assert main(["plugin-sign", str(key), "--key", str(key)]) == 1  # not a zip


# -- the market -------------------------------------------------------------------------------------------


class Shop:
    """A market list and its zips on a mock web."""

    def __init__(self, w: World) -> None:
        self.w = w
        self.entries: list[dict[str, Any]] = []
        self.zips: dict[str, bytes] = {}
        self.report = "https://example.com/report?p={id}&v={version}"
        w.app.state.fetch_transport = httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == INDEX:
            listed = {"format": 1, "report": self.report, "plugins": self.entries}
            return httpx.Response(200, json=listed)
        return httpx.Response(200, content=self.zips[url]) if url in self.zips else httpx.Response(404)

    def offer(self, version: str, *, seed: bytes | None = None, **more: Any) -> None:
        manifest = {"id": "cica.hello", "version": version, "name": "你好"}
        data = package(manifest)
        url = f"https://market.example/hello-{version}.zip"
        self.zips[url] = data
        sha = hashlib.sha256(data).hexdigest()
        entry = manifest | {"kinds": [], "url": url, "sha256": sha, "size": len(data)} | more
        if seed is not None:
            text = market.signed_text("cica.hello", version, sha)
            entry["author_key"] = base64.b64encode(ed25519.public_key(seed)).decode()
            entry["signature"] = base64.b64encode(ed25519.sign(seed, text)).decode()
        self.entries = [entry]

    async def listed(self) -> dict[str, Any]:
        (entry,) = (await self.w.http.get("/api/market")).json()["entries"]
        return entry

    async def get(self) -> httpx.Response:
        staged = await self.w.http.post("/api/market/cica.hello/stage")
        if staged.status_code != 200:
            return staged
        return await self.w.http.post(f"/api/plugin-packages/{staged.json()['stage']}/install")


async def test_an_upgrade_must_be_signed_by_the_same_author(world: World) -> None:  # noqa: F811
    shop = Shop(world)
    assert (await world.http.put("/api/market", json={"url": INDEX})).status_code == 200
    author, other = bytes(range(32)), bytes(32)
    shop.offer("1.0.0", seed=author)
    entry = await shop.listed()
    assert entry["signature"] == "signed" and "author_key" not in entry
    assert entry["report"] == "https://example.com/report?p=cica.hello&v=1.0.0"
    assert (await shop.get()).status_code == 201
    (mine,) = [p for p in (await world.http.get("/api/plugins")).json()["plugins"] if p["id"] == "cica.hello"]
    assert mine["signed"] is True and "author_key" not in mine

    shop.offer("1.1.0", seed=other)
    assert (await shop.listed())["signature"] == "changed"
    refused = await shop.get()
    assert refused.status_code == 409 and "不是同一个作者" in refused.json()["detail"]
    shop.offer("1.1.0")
    assert (await shop.get()).status_code == 409  # not signed at all any more
    shop.offer("1.1.0", seed=author)
    shop.entries[0]["signature"] = shop.entries[0]["signature"][::-1]
    assert (await shop.listed())["signature"] == "bad"
    assert (await shop.get()).status_code == 502
    shop.offer("1.1.0", seed=author)
    assert (await shop.get()).status_code == 201
    assert world.state.plugins.installed["cica.hello"][0].version == "1.1.0"
    # Taken off, it is anyone's again.
    await world.http.delete("/api/plugins/cica.hello")
    shop.offer("2.0.0", seed=other)
    assert (await shop.get()).status_code == 201


async def test_newer_versions_are_told_of(world: World) -> None:  # noqa: F811
    shop = Shop(world)
    await world.http.put("/api/market", json={"url": INDEX})
    shop.offer("1.9.0")
    assert (await shop.get()).status_code == 201
    keeper = world.state.market
    assert await keeper.look() == []
    shop.offer("1.10.0")
    assert await keeper.look() == [{"id": "cica.hello", "name": "你好", "from": "1.9.0", "to": "1.10.0"}]
    assert (await world.http.get("/api/market/updates")).json()["updates"][0]["to"] == "1.10.0"
    entry = await shop.listed()
    assert entry["newer"] is True and entry["installed"] == "1.9.0"
    shop.entries = []  # the list cannot say: nothing to tell
    assert await keeper.look() == []
    shop.offer("1.8.0")
    assert (await shop.listed())["newer"] is False  # an older one is no upgrade
    assert newer("0.10.0", "0.9.9") and not newer("1.0", "1.0.0")


async def test_the_nest_a_plugin_needs(world: World, tmp_path: Path) -> None:  # noqa: F811
    def read(needs: str) -> tuple[Any, list[str]]:
        (tmp_path / "plugin.json").write_text(
            json.dumps({"id": "cica.hello", "version": "1.0.0", "name": "你好", "nest": needs}),
            encoding="utf-8",
        )
        return read_manifest(tmp_path)

    for fine in ("", __version__, f">={__version__}", "0.1.0"):
        assert read(fine)[0] is not None, fine
    assert read("99.0.0")[1] == [f"它要 nest 99.0.0 以上，现在是 {__version__}：先更新 nest"]
    assert "最低版本号" in read("new")[1][0]
    shop = Shop(world)
    await world.http.put("/api/market", json={"url": INDEX})
    shop.offer("1.0.0", nest="99.0.0")
    assert (await shop.listed())["too_new"] is True
    assert "先更新 nest" in (await shop.get()).json()["detail"]


def test_the_example_plugin_is_one() -> None:
    folder = Path(__file__).resolve().parent.parent / "examples" / "plugins" / "hello"
    manifest, problems = read_manifest(folder)
    assert manifest is not None and not problems and manifest.trust == "guides"


# -- sticker packs ------------------------------------------------------------------------------------------


async def test_a_locked_picture_is_left_by_drawing_all_again(world: World) -> None:  # noqa: F811
    pack = await drawn_pack(world)
    slot = pack["slots"][0]
    path = f"/api/sticker-packs/slots/{slot['id']}"
    locked = (await world.http.patch(path, json={"locked": True})).json()
    assert locked["slots"][0]["locked"] == 1 and locked["slots"][1]["locked"] == 0
    drawn = await world.http.post(f"/api/sticker-packs/{pack['id']}/draw", json={"which": "all"})
    assert drawn.json()["queued"] == len(pack["slots"]) - 1
    unlocked = (await world.http.patch(path, json={"locked": False, "mood": "偷笑"})).json()
    assert (unlocked["slots"][0]["locked"], unlocked["slots"][0]["mood"]) == (0, "偷笑")


async def test_drawing_what_everyone_is_missing(world: World) -> None:  # noqa: F811
    await new_pack(world)
    await world.http.post(f"/api/sticker-packs/bots/{PONY}", json={"name": "她的"})
    await painter(world)
    assert (await world.http.get("/api/sticker-packs/missing")).json() == {
        "bots": [],
        "count": 0,
    }  # no pictures
    nothing = await world.http.post("/api/sticker-packs/draw-everyone")
    assert nothing.status_code == 409 and "没有要画的" in nothing.json()["detail"]
    await world.http.put(f"/api/bots/{WHALE}/avatar/custom", content=AVATAR)
    found = (await world.http.get("/api/sticker-packs/missing")).json()
    assert found["bots"] == [{"bot_id": WHALE, "name": "阿鲸", "count": found["count"]}]
    queued = (await world.http.post("/api/sticker-packs/draw-everyone")).json()
    assert queued["queued"] == found["count"] > 0


async def test_a_pack_is_shared_as_a_plugin_and_taken_by_another(world: World) -> None:  # noqa: F811
    pack = await drawn_pack(world)
    shared = await world.http.get(f"/api/sticker-packs/{pack['id']}/plugin", params={"author": "Cica"})
    assert shared.status_code == 200 and "cica.stickers-" in shared.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(shared.content)) as archive:
        meta = json.loads(archive.read("plugin.json"))
        listed = json.loads(archive.read("stickers/pack/pack.json"))
    assert meta["provides"] == {"stickers": ["stickers/pack"]}
    assert [i["mood"] for i in listed["items"]] == [s["mood"] for s in pack["slots"]]
    staged = (await world.http.put("/api/plugin-packages", content=shared.content)).json()
    assert staged["plugin"]["kinds"] == ["stickers"] and staged["plugin"]["trust"] == "content"
    installed = await world.http.post(f"/api/plugin-packages/{staged['stage']}/install")
    assert installed.status_code == 201

    (offered,) = (await world.http.get("/api/sticker-packs/from-plugins")).json()
    assert offered["count"] == len(pack["slots"])
    took = await world.http.post(
        f"/api/sticker-packs/bots/{PONY}/from-plugin",
        json={"plugin": offered["plugin"], "path": offered["path"]},
    )
    assert took.status_code == 201, took.text
    assert took.json()["name"] == pack["name"]
    wrong = await world.http.post(
        f"/api/sticker-packs/bots/{PONY}/from-plugin", json={"plugin": offered["plugin"], "path": "../x"}
    )
    assert wrong.status_code == 409


def test_a_sticker_pack_in_a_plugin_is_checked(tmp_path: Path) -> None:
    folder = tmp_path / "stickers" / "a"
    folder.mkdir(parents=True)
    manifest = {"id": "cica.s", "version": "1.0.0", "name": "s", "provides": {"stickers": ["stickers/a"]}}
    (tmp_path / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "01.png").write_bytes(picture("red"))

    def problems(items: Any) -> list[str]:
        (folder / "pack.json").write_text(json.dumps({"items": items}), encoding="utf-8")
        return read_manifest(tmp_path)[1]

    assert problems([{"mood": "开心", "file": "01.png"}]) == []
    assert "pack.json" in problems([])[0]
    assert "没有这张图" in problems([{"mood": "开心", "file": "02.png"}])[0]
    assert "mood" in problems([{"mood": "", "file": "01.png"}])[0]
    assert "file" in problems([{"mood": "开心", "file": "../../x.exe"}])[0]
