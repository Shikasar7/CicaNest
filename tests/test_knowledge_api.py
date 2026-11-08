"""The console's 「资料」 page: adding a help-doc base (only a folder with help files), binding a group to it,
reloading, and 「试问」 showing what retrieval finds (here with the model servers down: keywords only)."""

from __future__ import annotations

from pathlib import Path

import httpx

from nest.core.events import ChannelInfo, ChannelRef
from nest.storage.accounts import upsert_account
from tests.conftest import TOKEN
from tests.test_project_answers import FEATURES, OVERVIEW

AUTH = {"authorization": f"Bearer {TOKEN}"}


async def named_group(client: httpx.AsyncClient) -> str:
    state = client.app.state  # type: ignore[attr-defined]
    await upsert_account(
        state.db, platform="qq", self_id="1", name="鲸", http_url="http://x", ws_url="ws://x"
    )
    await state.store.name_channels([ChannelInfo(ChannelRef("qq", "1", "group", "42"), "测试群二")])
    return "qq:group:42"


async def test_a_base_is_added_bound_reloaded_and_asked(client: httpx.AsyncClient, tmp_path: Path) -> None:
    group = await named_group(client)
    empty = tmp_path / "empty"
    empty.mkdir()
    for folder, why in ((tmp_path / "nowhere", "找不到文件夹"), (empty, "没有资料")):
        response = await client.post(
            "/api/knowledge", json={"name": "Cica", "folder": str(folder)}, headers=AUTH
        )
        assert response.status_code == 422 and why in response.json()["detail"]

    docs = tmp_path / "help"
    docs.mkdir()
    (docs / "overview.md").write_text(OVERVIEW, encoding="utf-8")
    (docs / "features.md").write_text(FEATURES, encoding="utf-8")
    response = await client.post(
        "/api/knowledge", json={"name": "DemoApp", "folder": str(docs)}, headers=AUTH
    )
    assert response.status_code == 201
    base = response.json()
    assert base["sections"] == 3 and base["files"] == {"overview.md": 1, "features.md": 2}
    assert base["vectors"] is False and base["groups"] == []  # the embedding server is down in tests
    again = await client.post("/api/knowledge", json={"name": "DemoApp", "folder": str(docs)}, headers=AUTH)
    assert again.status_code == 409

    bind = await client.put(
        "/api/knowledge/groups", json={"group_key": group, "base_id": base["id"]}, headers=AUTH
    )
    assert bind.status_code == 200
    page = (await client.get("/api/knowledge", headers=AUTH)).json()
    assert page["groups"] == [{"group_key": group, "name": "测试群二", "base_id": base["id"]}]
    assert page["bases"][0]["groups"] == [group]
    assert page["models"]["embed_up"] is False and page["models"]["rerank_up"] is False
    service = client.app.state.knowledge  # type: ignore[attr-defined]
    assert service.base_for(group) is not None

    asked = (
        await client.post(f"/api/knowledge/{base['id']}/ask", json={"question": "朗读怎么开"}, headers=AUTH)
    ).json()
    assert asked["reranked"] is False and "向量模型没开" in asked["degraded"]
    assert [s["id"] for s in asked["sections"]] == ["tts"]
    assert asked["steps"]["关键词"][0] == {
        "id": "tts",
        "title": "朗读",
        "score": asked["steps"]["关键词"][0]["score"],
    }

    (docs / "faq.md").write_text("# 常见问题\n\n## 要钱吗 {#faq-cost}\n\n不要钱。\n", encoding="utf-8")
    reloaded = (await client.post(f"/api/knowledge/{base['id']}/reload", headers=AUTH)).json()
    assert reloaded["sections"] == 4

    unbind = await client.put(
        "/api/knowledge/groups", json={"group_key": group, "base_id": None}, headers=AUTH
    )
    assert unbind.status_code == 200 and service.base_for(group) is None
    assert (await client.delete(f"/api/knowledge/{base['id']}", headers=AUTH)).status_code == 200
    assert (await client.get("/api/knowledge", headers=AUTH)).json()["bases"] == []


async def test_only_known_groups_and_bases_can_be_bound(client: httpx.AsyncClient) -> None:
    await named_group(client)
    unknown = await client.put(
        "/api/knowledge/groups", json={"group_key": "qq:group:9", "base_id": None}, headers=AUTH
    )
    assert unknown.status_code == 404
    missing = await client.put(
        "/api/knowledge/groups", json={"group_key": "qq:group:42", "base_id": 7}, headers=AUTH
    )
    assert missing.status_code == 404


async def test_unanswered_questions_are_listed_answered_into_the_docs_and_ignored(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    group = await named_group(client)
    docs = tmp_path / "help"
    docs.mkdir()
    (docs / "features.md").write_text(FEATURES, encoding="utf-8")
    base = (
        await client.post("/api/knowledge", json={"name": "Cica", "folder": str(docs)}, headers=AUTH)
    ).json()
    bad = await client.patch(f"/api/knowledge/{base['id']}", json={"forward_to": "abc"}, headers=AUTH)
    assert bad.status_code == 422
    saved = await client.patch(f"/api/knowledge/{base['id']}", json={"forward_to": "10086"}, headers=AUTH)
    assert saved.json()["forward_to"] == "10086"
    service = client.app.state.knowledge  # type: ignore[attr-defined]
    for question in ("能不能导出 PDF", "有没有深色主题"):
        await service.report_gap(
            base_id=base["id"],
            group_key=group,
            account_id="qq:1",
            message_id=None,
            asker="小明",
            question=question,
            answer="不确定",
            reason="not_found",
            forward=False,
        )
    listed = (await client.get(f"/api/knowledge/{base['id']}/gaps", headers=AUTH)).json()
    assert [g["question"] for g in listed["gaps"]] == ["有没有深色主题", "能不能导出 PDF"]
    assert listed["gaps"][0]["group_name"] == "测试群二" and listed["notes"] == []
    dark_theme, export_pdf = listed["gaps"]

    answered = await client.post(
        f"/api/knowledge/gaps/{export_pdf['id']}/answer",
        json={"title": "导出 PDF", "body": "目前不能导出 PDF，只能导出文本。"},
        headers=AUTH,
    )
    assert answered.status_code == 200
    asked = (
        await client.post(f"/api/knowledge/{base['id']}/ask", json={"question": "能导出吗"}, headers=AUTH)
    ).json()
    assert asked["sections"][0]["id"] == f"note-{answered.json()['note_id']}"  # searchable at once
    assert (
        await client.post(f"/api/knowledge/gaps/{dark_theme['id']}/ignored", headers=AUTH)
    ).status_code == 200
    assert (await client.get(f"/api/knowledge/{base['id']}/gaps", headers=AUTH)).json()["gaps"] == []
    every = (await client.get(f"/api/knowledge/{base['id']}/gaps?status=all", headers=AUTH)).json()
    assert {g["status"] for g in every["gaps"]} == {"answered", "ignored"} and len(every["notes"]) == 1

    note = every["notes"][0]["id"]
    assert (await client.delete(f"/api/knowledge/notes/{note}", headers=AUTH)).status_code == 200
    reopened = (await client.get(f"/api/knowledge/{base['id']}/gaps", headers=AUTH)).json()["gaps"]
    assert [g["question"] for g in reopened] == ["能不能导出 PDF"]  # its answer is gone: open again


async def test_a_source_folder_is_set_and_shown(client: httpx.AsyncClient, tmp_path: Path) -> None:
    docs = tmp_path / "help"
    docs.mkdir()
    (docs / "features.md").write_text(FEATURES, encoding="utf-8")
    base = (
        await client.post("/api/knowledge", json={"name": "Cica", "folder": str(docs)}, headers=AUTH)
    ).json()
    assert base["source_dir"] == "" and base["source"] is None
    missing = await client.patch(
        f"/api/knowledge/{base['id']}", json={"source_dir": str(tmp_path / "nowhere")}, headers=AUTH
    )
    assert missing.status_code == 422 and "找不到文件夹" in missing.json()["detail"]
    source = tmp_path / "src"
    (source / "web").mkdir(parents=True)
    (source / "web" / "app.js").write_text("console.log(1)\n", encoding="utf-8")
    saved = (
        await client.patch(f"/api/knowledge/{base['id']}", json={"source_dir": str(source)}, headers=AUTH)
    ).json()
    assert saved["source_dir"] == str(source)
    assert saved["source"] == {"ready": True, "commit": "", "date": "", "files": 1}  # not a git checkout
    url = f"/api/knowledge/{base['id']}"
    cleared = (await client.patch(url, json={"source_dir": ""}, headers=AUTH)).json()
    assert cleared["source_dir"] == "" and cleared["source"] is None
    service = client.app.state.knowledge  # type: ignore[attr-defined]
    gap_id = await service.report_gap(
        base_id=base["id"],
        group_key="qq:group:42",
        account_id="qq:1",
        message_id=None,
        asker="小明",
        question="设置为什么不更新",
        answer="不确定",
        reason="not_found",
        forward=False,
    )
    files = ["web/a.js", "web/b.js"]
    await service.gaps.set_analysis(gap_id, "回复里没有保存块就不改", sure=True, files=files)
    [gap] = (await client.get(f"/api/knowledge/{base['id']}/gaps", headers=AUTH)).json()["gaps"]
    assert gap["analysis"] == "回复里没有保存块就不改" and gap["analysis_sure"] == 1
    assert gap["analysis_files"] == "web/a.js\nweb/b.js"
