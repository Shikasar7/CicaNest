"""项目资料 at run time: each base loaded and indexed, which group answers from which, and finding the
sections for a question: keyword search
(BM25 plus exact error matching) and vector search (bge-m3) fused by rank, then a reranker
(bge-reranker-v2-m3) orders the candidates and its best score says whether the docs answer at all.
Sections are embedded and reranked without the header (it did not help, and hurt the reranker).

Then, when the caller passes `ask` (the answering bot's model), that model picks from the reranked
candidates which sections the bot reads (nest.knowledge.choose): it ranks better than
bge, but bge stays the judge of whether the docs answer at all.

A base reloads by itself when its files change. When the embedding or rerank server is down, retrieval
goes on with what is left and says so (degraded), rather than failing the answer.

A base may also name its project's source checkout: a question the docs do not answer is then worked out
from the source (看源码, nest.knowledge.investigate), one at a time per base and at most INVESTIGATIONS an
hour, so a busy group cannot spend the model's quota on it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from nest.adapters.base import parse_recipient
from nest.core.tones import Tone
from nest.knowledge.choose import CHOOSE_MAX_TOKENS, build_choose_prompt, parse_choice
from nest.knowledge.dense import DenseIndex, EmbeddingClient, EmbeddingModel, document_text, text_key
from nest.knowledge.gaps import GapForwarder
from nest.knowledge.investigate import Finding, investigate
from nest.knowledge.lexical import ErrorIndex, LexicalIndex, rrf
from nest.knowledge.loader import FILES, Section, load
from nest.knowledge.rerank import RerankClient, RerankModel
from nest.knowledge.source import SourceTree
from nest.llm.base import ChatMessage
from nest.storage.knowledge import GapRepo, KnowledgeBase, KnowledgeRepo, Note

log = logging.getLogger(__name__)

# The answering bot's model, for picking sections: messages and max_tokens in, its text out.
Ask = Callable[[list[ChatMessage], int], Awaitable[str]]

CANDIDATES = 30  # each search ranks this many; the reranker orders this many
SHOWN = 6  # sections handed to the answer
CHAR_BUDGET = 8000  # …and at most this much text of them
# Rerank score below which the docs are taken not to answer (bge-reranker-v2-m3): low enough that answerable
# questions pass, high enough that most the docs cannot answer are turned down.
FLOOR = 0.17
RETRY_SECONDS = 60.0  # a model server that was down is tried again after this long
TIMEOUT = 20.0
INVESTIGATIONS = 6  # 看源码 per base per hour at most
INVESTIGATE_SECONDS = 300.0  # one 看源码 gives up after this long


@dataclass
class Retrieval:
    base: str
    sections: list[tuple[Section, float]]  # best first; the score is the reranker's (or the fused rank's)
    found: bool  # the docs answer it (the best rerank score is above FLOOR), whatever the model picked
    best: float
    reranked: bool = True  # False: the reranker was down, sections are in fused order and best means nothing
    chosen: bool = False  # the answering bot's model picked the sections (otherwise the reranker's top)
    degraded: str = ""  # why retrieval had to do without a model, or ""
    steps: dict[str, list[tuple[str, float]]] = field(default_factory=dict)  # each search's top, for 试问
    took_ms: float = 0.0


@dataclass
class LoadedBase:
    base: KnowledgeBase
    sections: list[Section] = field(default_factory=list)
    lexical: LexicalIndex | None = None
    errors: ErrorIndex | None = None
    dense: DenseIndex | None = None
    signature: tuple = ()
    loaded_at: float = 0.0
    problem: str = ""  # why the base could not load, or ""
    dense_tried_at: float = 0.0

    @property
    def by_file(self) -> dict[str, list[Section]]:
        out: dict[str, list[Section]] = {}
        for section in self.sections:
            out.setdefault(section.file, []).append(section)
        return out


def _within_budget(sections: Sequence[tuple[Section, float]]) -> list[tuple[Section, float]]:
    """The first SHOWN sections, stopping before one that would take the text past CHAR_BUDGET (the first
    always goes in)."""
    shown: list[tuple[Section, float]] = []
    used = 0
    for section, score in sections[:SHOWN]:
        size = len(section.text())
        if shown and used + size > CHAR_BUDGET:
            break
        shown.append((section, score))
        used += size
    return shown


def note_section(note: Note) -> Section:
    """An answer added in the console, searched like the docs' own sections."""
    return Section(f"note-{note.id}", "notes.md", "", note.title, {}, note.body)


def _signature(folder: str) -> tuple:
    """Blocking. What changes when a help file does: names, modification times, sizes."""
    out = []
    for name in FILES:
        with contextlib.suppress(OSError):
            stat = (Path(folder).expanduser() / name).stat()
            out.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(out)


class KnowledgeService:
    def __init__(
        self,
        repo: KnowledgeRepo,
        *,
        embed: EmbeddingModel,
        rerank: RerankModel,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.repo = repo
        self.gaps = GapRepo(repo.db)
        self.forwarder: GapForwarder | None = None  # set once there is a way to send (app.py)
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT, trust_env=False)
        self.embedder = EmbeddingClient(embed, self._client)
        self.reranker = RerankClient(rerank, self._client)
        self.bases: dict[int, LoadedBase] = {}
        self.groups: dict[str, int] = {}
        self._reading: dict[int, asyncio.Lock] = {}  # base id -> one 看源码 at a time
        self._read_at: dict[int, list[float]] = {}  # base id -> when each 看源码 of the last hour started

    async def start(self) -> None:
        self.groups = await self.repo.groups()
        for base in await self.repo.bases():
            self.bases[base.id] = LoadedBase(base)
            await self._load(self.bases[base.id])

    async def stop(self) -> None:
        if self.forwarder is not None:
            await self.forwarder.stop()
        await self._client.aclose()

    async def report_gap(
        self,
        *,
        base_id: int,
        group_key: str,
        account_id: str,
        message_id: int | None,
        asker: str,
        question: str,
        answer: str,
        reason: str,
        forward: bool,
        hold: bool = False,
    ) -> int:
        """Record a project question the docs did not answer; forward it if `forward` (the bot is live),
        unless `hold`: the source is being read first, and finish_gap forwards it with what that showed."""
        gap_id = await self.gaps.add_gap(
            base_id=base_id,
            group_key=group_key,
            account_id=account_id,
            message_id=message_id,
            asker=asker,
            question=question[:1000],
            answer=answer[:1000],
            reason=reason,
        )
        loaded = self.bases.get(base_id)
        if forward and not hold and self.forwarder is not None and loaded is not None:
            self.forwarder.notify(base_id, loaded.base.name)
        return gap_id

    async def finish_gap(self, gap_id: int, base_id: int, finding: Finding | None, *, forward: bool) -> None:
        """看源码 is done (finding None: it could not run): keep what it found, then forward the question."""
        if finding is not None and finding.answer:
            await self.gaps.set_analysis(gap_id, finding.answer, sure=finding.sure, files=finding.files)
        loaded = self.bases.get(base_id)
        if forward and self.forwarder is not None and loaded is not None:
            self.forwarder.notify(base_id, loaded.base.name)

    def can_read_source(self, loaded: LoadedBase) -> bool:
        """The base names a source checkout that exists, and this hour's 看源码 are not used up."""
        if not loaded.base.source_dir or not Path(loaded.base.source_dir).expanduser().is_dir():
            return False
        hour_ago = time.time() - 3600
        recent = [at for at in self._read_at.get(loaded.base.id, []) if at > hour_ago]
        self._read_at[loaded.base.id] = recent
        return len(recent) < INVESTIGATIONS

    async def read_source(
        self,
        loaded: LoadedBase,
        ask: Ask,
        *,
        name: str,
        question: str,
        replied: str,
        seen: list[str],
        tone: Tone | None = None,
    ) -> Finding:
        """看源码 for one question (the caller checked can_read_source). Never raises: a model or file
        problem comes back as the finding's problem."""
        base_id = loaded.base.id
        self._read_at.setdefault(base_id, []).append(time.time())
        async with self._reading.setdefault(base_id, asyncio.Lock()):
            tree = SourceTree(loaded.base.source_dir)
            try:
                return await asyncio.wait_for(
                    investigate(
                        tree,
                        ask,
                        name=name,
                        project=loaded.base.name,
                        question=question,
                        replied=replied,
                        seen=seen,
                        tone=tone,
                    ),
                    INVESTIGATE_SECONDS,
                )
            except TimeoutError:
                return Finding(problem=f"超过 {INVESTIGATE_SECONDS:.0f} 秒还没看完")
            except Exception as exc:  # the model being down must not take the brain's task with it
                log.info("项目资料「%s」看源码失败：%s", loaded.base.name, exc)
                return Finding(problem=f"看源码出错（{type(exc).__name__}：{exc}）")

    async def refresh(self) -> None:
        """Bases and group bindings as stored now (after the console changed them)."""
        self.groups = await self.repo.groups()
        stored = {base.id: base for base in await self.repo.bases()}
        for base_id in list(self.bases):
            if base_id not in stored:
                del self.bases[base_id]
        for base_id, base in stored.items():
            loaded = self.bases.get(base_id)
            if loaded is None or loaded.base.folder != base.folder:
                self.bases[base_id] = LoadedBase(base)
                await self._load(self.bases[base_id])
            else:
                loaded.base = base

    async def reload(self, base_id: int) -> LoadedBase | None:
        loaded = self.bases.get(base_id)
        if loaded is not None:
            await self._load(loaded)
        return loaded

    def base_for(self, group_key: str) -> LoadedBase | None:
        base_id = self.groups.get(group_key)
        loaded = self.bases.get(base_id) if base_id is not None else None
        return loaded if loaded is not None and loaded.sections else None

    def base_kept_by(self, user_id: str, platform: str = "qq") -> LoadedBase | None:
        """The base `user_id` keeps (unanswered questions are forwarded to them): in a private chat with
        that person the bot answers project questions from it too."""
        for loaded in self.bases.values():
            if user_id and parse_recipient(loaded.base.forward_to) == (platform, user_id) and loaded.sections:
                return loaded
        return None

    async def _load(self, loaded: LoadedBase) -> None:
        folder = loaded.base.folder
        try:
            sections = await asyncio.to_thread(lambda: load(Path(folder).expanduser()))
        except (OSError, ValueError) as exc:
            loaded.problem = f"读不了资料：{exc}"
            log.warning("项目资料「%s」%s", loaded.base.name, loaded.problem)
            return
        if not sections:
            loaded.problem = f"{folder} 里没有资料（要有 features.md、errors.md 这类文件）"
            return
        sections += [note_section(note) for note in await self.gaps.notes(loaded.base.id)]
        loaded.sections = sections
        loaded.lexical = LexicalIndex(sections)
        loaded.errors = ErrorIndex(sections)
        loaded.signature = await asyncio.to_thread(_signature, folder)
        loaded.loaded_at = time.time()
        loaded.problem = ""
        loaded.dense = None
        await self._embed(loaded)
        note = "" if loaded.dense else "（向量模型没开）"
        log.info("项目资料「%s」已载入：%d 节%s", loaded.base.name, len(sections), note)

    async def _embed(self, loaded: LoadedBase) -> None:
        loaded.dense_tried_at = time.time()
        model = self.embedder.model.name
        try:
            cache = await self.repo.vectors(model)
            loaded.dense = await DenseIndex.build(loaded.sections, self.embedder, header=False, cache=cache)
        except httpx.HTTPError as exc:
            log.info("项目资料「%s」暂时不用向量检索：%s", loaded.base.name, exc)
            return
        used = {text_key(model, document_text(s, header=False)) for s in loaded.sections}
        await self.repo.keep_vectors(model, cache, used)

    @staticmethod
    async def _choose(
        name: str, question: str, ranked: Sequence[tuple[Section, float]], ask: Ask, degraded: list[str]
    ) -> list[tuple[Section, float]] | None:
        """The candidates the model picked, best first (possibly none); None when it could not say."""
        try:
            text = await ask(build_choose_prompt(name, question, [s for s, _ in ranked]), CHOOSE_MAX_TOKENS)
        except Exception as exc:  # the model being down or slow must not cost the answer its sections
            log.info("项目资料「%s」请模型挑资料失败：%s", name, exc)
            degraded.append(f"模型没挑成资料（{type(exc).__name__}），用重排的结果")
            return None
        chosen = parse_choice(text, [s.id for s, _ in ranked])
        if chosen is None:
            degraded.append("模型挑资料的回答看不懂，用重排的结果")
            return None
        scores = {s.id: (s, score) for s, score in ranked}
        return [scores[i] for i in chosen]

    async def retrieve(
        self, loaded: LoadedBase, question: str, keywords: Sequence[str] = (), ask: Ask | None = None
    ) -> Retrieval:
        """The sections that answer `question`. `keywords`: extra search words (the sorting model's). `ask`:
        the answering bot's model, to pick the sections from the reranked candidates (nest.knowledge.choose);
        when it fails or its answer is unreadable, the reranker's top sections are used."""
        start = time.perf_counter()
        if await asyncio.to_thread(_signature, loaded.base.folder) != loaded.signature:
            await self._load(loaded)
        elif loaded.dense is None and time.time() - loaded.dense_tried_at > RETRY_SECONDS:
            await self._embed(loaded)
        assert loaded.lexical is not None and loaded.errors is not None
        degraded = []
        steps: dict[str, list[tuple[str, float]]] = {}
        query = " ".join([question, *keywords])
        keyword = loaded.lexical.search(query, CANDIDATES)
        errors = loaded.errors.search(question)
        steps["关键词"] = [(s.id, score) for s, score in keyword[:10]]
        steps["报错原文"] = [(s.id, float(n)) for s, n in errors[:10]]
        lists = [[s for s, _ in keyword], [s for s, _ in errors]]
        if loaded.dense is not None:
            try:
                near = loaded.dense.search(await self.embedder.embed_query(question), CANDIDATES)
                lists.insert(1, [s for s, _ in near])
                steps["向量"] = [(s.id, score) for s, score in near[:10]]
            except httpx.HTTPError as exc:
                degraded.append(f"向量模型没响应（{type(exc).__name__}）")
        else:
            degraded.append("向量模型没开")
        fused = rrf(lists)[:CANDIDATES]
        steps["融合"] = [(s.id, score) for s, score in fused[:10]]
        candidates = [s for s, _ in fused]
        try:
            texts = [document_text(s, header=False) for s in candidates]
            scores = await self.reranker.scores(question, texts)
            ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: -pair[1])
            best = ranked[0][1] if ranked else 0.0
            found = best >= FLOOR
            steps["重排"] = [(s.id, score) for s, score in ranked[:10]]
        except httpx.HTTPError as exc:
            degraded.append(f"重排模型没响应（{type(exc).__name__}）")
            ranked, best = fused, 0.0
            found = bool(fused)
        reranked = "重排" in steps
        picked = None
        if ask is not None and ranked:
            picked = await self._choose(loaded.base.name, question, ranked, ask, degraded)
        if picked is not None:
            steps["大模型挑选"] = [(s.id, score) for s, score in picked]
            shown = _within_budget(picked)
        else:
            # The reranker's top, down to where it says the rest does not answer it.
            top = [(s, score) for s, score in ranked[:SHOWN] if not reranked or score >= FLOOR]
            shown = _within_budget(top) if found else []
        return Retrieval(
            base=loaded.base.name,
            sections=shown,
            found=found,
            best=best,
            reranked=reranked,
            chosen=picked is not None,
            degraded="；".join(degraded),
            steps=steps,
            took_ms=(time.perf_counter() - start) * 1000,
        )

    @staticmethod
    def overview(loaded: LoadedBase) -> str:
        """What the project is (overview.md), sent with every project answer."""
        return "\n\n".join(s.text() for s in loaded.by_file.get("overview.md", []))

    @staticmethod
    def feature_titles(loaded: LoadedBase) -> str:
        """Every feature's title, by area: lets the model say a feature does not exist."""
        areas: dict[str, list[str]] = {}
        for section in loaded.by_file.get("features.md", []):
            areas.setdefault(section.area or "其他", []).append(section.title)
        return "\n".join(f"{area}：{'、'.join(titles)}" for area, titles in areas.items())

    async def models_up(self) -> dict[str, bool]:
        async def up(url: str) -> bool:
            try:
                return (await self._client.get(f"{url.rstrip('/')}/health", timeout=1.5)).status_code == 200
            except httpx.HTTPError:
                return False

        embed, rerank = await asyncio.gather(up(self.embedder.model.url), up(self.reranker.model.url))
        return {"embed": embed, "rerank": rerank}
