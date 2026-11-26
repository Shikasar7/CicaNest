"""Gemini's own API (generateContent): Google AI Studio's Gemini API, or Vertex AI (Agent Platform).

The address is everything before /models/<model>:
- Gemini API: https://generativelanguage.googleapis.com/v1beta, with an API key.
- Vertex AI: https://aiplatform.googleapis.com/v1/projects/<project>/locations/<place>/publishers/google
  (or a regional host, <place>-aiplatform.googleapis.com), with an API key, or with a JSON credential in the
  key field: a service account's JSON key, or the file `gcloud auth application-default login` writes. An
  hour-long access token is made from it (google_auth) and kept until shortly before it runs out. With a
  credential, the address may stop at https://aiplatform.googleapis.com: its project fills in the rest
  (location global). Vertex AI's OpenAI-compatible address (…/endpoints/openapi) is taken as its own one.

The system prompt goes as systemInstruction, the turns as contents (one side's turns in a row joined, as the
API wants them taking turns), thinking at the option `effort` (thinkingLevel; a token budget for Gemini 2.x),
the safety filters at their loosest (Google's own check still runs). Tools of ours go as function
declarations; the model's own turn is handed back as it came, so its thought signatures go with it.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

from nest.llm import google_auth
from nest.llm.base import ChatMessage, Completion, ProviderError, TextSink, Tool, ToolCall, ToolRunner, Turn
from nest.llm.http import client, is_sse, scrub, sse, status_error, transport_error

EFFORTS = ("low", "medium", "high")
DEFAULT_EFFORT = "low"  # a chat answer wants to be quick
BUDGETS = {"low": 1024, "medium": 8192, "high": 24576}  # Gemini 2.x thinks to a token budget
SAFETY = [
    {"category": category, "threshold": "BLOCK_NONE"}
    for category in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]
MAX_TOOL_ROUNDS = 10  # calls that may ask for our tools, per answer; the next one may not
TOOL_RESULT_CHARS = 12_000
MODEL_PREFIX = re.compile(r"^(?:models/|publishers/google/models/|google/)")
VERTEX_HOST = re.compile(r"^(?:([a-z0-9-]+)-)?aiplatform\.googleapis\.com$")


def model_name(model: str) -> str:
    """The model as the address names it: without the `models/` Gemini's list puts before it, or the
    `google/` Vertex AI's OpenAI-compatible address wants."""
    return MODEL_PREFIX.sub("", model.strip())


def api_base(base_url: str, project: str = "") -> str:
    """The address the models hang off. Vertex AI's OpenAI-compatible one becomes its own (publishers/google);
    a bare Vertex AI host is completed with the credential's project (location: global, or the host's
    region)."""
    base = base_url.strip().rstrip("/")
    base = re.sub(r"/endpoints/openapi$", "/publishers/google", base)
    parts = urlsplit(base)
    found = VERTEX_HOST.match(parts.hostname or "")
    if found and project and "/projects/" not in parts.path:
        place = found.group(1) or "global"
        return f"{parts.scheme}://{parts.netloc}/v1/projects/{project}/locations/{place}/publishers/google"
    return base


def _image(url: str) -> dict[str, Any]:
    """A data URL ("data:image/png;base64,…") as an inline picture."""
    header, _, data = url.partition(",")
    mime = header.removeprefix("data:").removesuffix(";base64")
    if not header.startswith("data:") or not header.endswith(";base64") or not data:
        raise ProviderError("图片格式不对，只能发 base64 的 data URL", retryable=False)
    return {"inlineData": {"mimeType": mime, "data": data}}


def request_parts(messages: Sequence[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    """(system text, contents): every system message joined into one; the turns with one side's turns in a
    row joined. The conversation starts with the user."""
    system = "\n\n".join(m.content for m in messages if m.role == "system" and m.content.strip())
    contents: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        role = "model" if message.role == "assistant" else "user"
        parts: list[dict[str, Any]] = [_image(url) for url in message.images]
        if message.content or not parts:
            parts.append({"text": message.content})
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    if not contents or contents[0]["role"] != "user":
        contents.insert(0, {"role": "user", "parts": [{"text": "（继续）"}]})
    return system, contents


def declarations(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {"name": t.name, "description": t.description, "parametersJsonSchema": dict(t.parameters)}
                for t in tools
            ]
        }
    ]


def _answer(data: Any) -> tuple[dict[str, Any], str, bool]:
    """(the model's turn as it came, its text without thoughts, whether it hit the output limit)."""
    if not isinstance(data, dict):
        raise ProviderError("模型返回的格式看不懂", retryable=False)
    candidates = data.get("candidates")
    if not candidates:
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise ProviderError(f"Gemini 拒绝回答这段对话（{blocked}）", retryable=False)
        raise ProviderError("模型返回的格式看不懂", retryable=False)
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
    parts = [p for p in content.get("parts") or [] if isinstance(p, dict)]
    reason = candidate.get("finishReason")
    if not parts and reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
        raise ProviderError(f"Gemini 没有回答（{reason}）", retryable=False)
    text = "".join(str(p.get("text") or "") for p in parts if not p.get("thought"))
    return {"role": "model", "parts": parts}, text, reason == "MAX_TOKENS"


def _calls(turn: dict[str, Any]) -> list[ToolCall]:
    calls = []
    for number, part in enumerate(turn["parts"]):
        call = part.get("functionCall")
        if isinstance(call, dict):
            given = call.get("args") if isinstance(call.get("args"), dict) else {}
            calls.append(
                ToolCall(str(call.get("id") or f"call_{number}"), str(call.get("name") or ""), given)
            )
    return calls


def _results(results: Sequence[tuple[ToolCall, str]]) -> dict[str, Any]:
    """The tools' results, as the user's next turn."""
    parts = []
    for call, result in results:
        response: dict[str, Any] = {"name": call.name, "response": {"result": result[:TOOL_RESULT_CHARS]}}
        if not call.id.startswith("call_"):
            response["id"] = call.id
        parts.append({"functionResponse": response})
    return {"role": "user", "parts": parts}


def _usage(data: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = data.get("usageMetadata") or {}
    if usage.get("promptTokenCount") is None:
        return None, None
    written = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
    return usage.get("promptTokenCount"), written


class Gemini:
    streams = True

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        temperature: float | None = None,
        effort: str = DEFAULT_EFFORT,
        extra_body: dict[str, Any] | None = None,
        proxy: str | None = None,
        timeout: float = 40.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.model = model_name(model)
        try:
            self._credential = google_auth.credential(api_key)
        except ValueError:
            self._credential = None
        self._key = "" if self._credential is not None else api_key
        project = self._credential.project if self._credential is not None else ""
        self._base = api_base(base_url, project)
        self._temperature = temperature
        self._effort = effort if effort in EFFORTS else DEFAULT_EFFORT
        self._extra_body = dict(extra_body or {})
        self._http = client(timeout=timeout, proxy=proxy, transport=transport)
        self._token = ""  # the last access token made from the credential, scrubbed from errors
        self._cannot_stream = False

    def _thinking(self) -> dict[str, Any]:
        if re.search(r"gemini-2\.", self.model, re.IGNORECASE):
            return {"thinkingBudget": BUDGETS[self._effort]}
        return {"thinkingLevel": self._effort}

    def _body(
        self,
        system: str,
        contents: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float | None,
        tools: Sequence[Tool] = (),
        words_only: bool = False,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {"maxOutputTokens": max_tokens, "thinkingConfig": self._thinking()}
        chosen = temperature if temperature is not None else self._temperature
        if chosen is not None:
            config["temperature"] = chosen
        body: dict[str, Any] = {"contents": contents, "generationConfig": config, "safetySettings": SAFETY}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = declarations(tools)
            if words_only:
                body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        return {**body, **self._extra_body}

    async def _headers(self) -> dict[str, str]:
        if self._credential is not None:
            try:
                self._token = await google_auth.access_token(self._credential, self._http)
            except google_auth.AuthError as exc:
                raise ProviderError(str(exc), retryable=exc.retryable) from exc
            headers = {"authorization": f"Bearer {self._token}"}
            if self._credential.kind == "authorized_user" and self._credential.project:
                # A person's own login is billed to the project named here (gcloud's quota project).
                headers["x-goog-user-project"] = self._credential.project
            return headers
        return {"x-goog-api-key": self._key} if self._key else {}

    def _url(self, streamed: bool) -> str:
        if streamed:
            return f"{self._base}/models/{self.model}:streamGenerateContent?alt=sse"
        return f"{self._base}/models/{self.model}:generateContent"

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
        tools: Sequence[Tool] = (),
        run: ToolRunner | None = None,
        on_text: TextSink | None = None,
    ) -> Completion:
        system, contents = request_parts(messages)
        started = time.monotonic()
        used: list[tuple[str, str]] = []
        prompt_tokens = completion_tokens = 0
        counted = False
        offered = tools if run is not None else ()
        # The last call may use no tool: it answers in words, saying how far it got.
        for round_ in range(MAX_TOOL_ROUNDS + 1):
            body = self._body(
                system,
                contents,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=offered,
                words_only=round_ == MAX_TOOL_ROUNDS,
            )
            data = await self._streamed(body, on_text) if on_text is not None else None
            if data is None:
                data = await self._post(body)
                self._cannot_stream = self._cannot_stream or on_text is not None
            turn, text, cut = _answer(data)
            asked, written = _usage(data)
            if asked is not None:
                counted = True
                prompt_tokens += asked
                completion_tokens += written or 0
            calls = _calls(turn)
            if not calls or run is None or round_ == MAX_TOOL_ROUNDS:
                break
            if on_text is not None:
                on_text(None)  # what it said before the tool is not the answer
            contents.append(turn)
            results = []
            for call in calls:
                result = await run(call.name, call.given)
                used.append((call.name, json.dumps(call.given, ensure_ascii=False)[:200]))
                results.append((call, result))
            contents.append(_results(results))
        return Completion(
            text=text,
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=prompt_tokens if counted else None,
            completion_tokens=completion_tokens if counted else None,
            cut=cut,
            tools_used=tuple(used),
        )

    async def converse(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[Tool],
        max_tokens: int,
        state: Any = None,
        results: Sequence[tuple[ToolCall, str]] = (),
        words_only: bool = False,
    ) -> Turn:
        """One round with tools (任务): the caller runs what it asks for and hands back the results. state:
        (system text, contents so far)."""
        if state is None:
            system, contents = request_parts(messages)
        else:
            system, contents = state[0], list(state[1])
        if results:
            contents.append(_results(results))
        body = self._body(
            system, contents, max_tokens=max_tokens, temperature=None, tools=tools, words_only=words_only
        )
        started = time.monotonic()
        data = await self._post(body)
        turn, text, cut = _answer(data)
        asked, written = _usage(data)
        return Turn(
            text=text,
            calls=tuple(_calls(turn)),
            state=(system, [*contents, turn]),
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=asked,
            completion_tokens=written,
            cut=cut,
        )

    async def _post(self, body: dict[str, Any]) -> Any:
        headers = await self._headers()
        try:
            response = await self._http.post(self._url(False), json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if response.status_code >= 400:
            raise status_error(response, self._key, self._token)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("模型返回的格式看不懂", retryable=False) from exc

    async def _streamed(self, body: dict[str, Any], on_text: TextSink) -> dict[str, Any] | None:
        """The same call, streamed: what it comes to, in the form of an answer that was not. None when the
        address refuses it (it is then asked the usual way, and from now on when that works)."""
        if self._cannot_stream:
            return None
        headers = await self._headers()
        parts: list[dict[str, Any]] = []
        last: dict[str, Any] = {}
        finish: Any = None
        heard = False
        try:
            async with self._http.stream("POST", self._url(True), json=body, headers=headers) as response:
                if response.status_code >= 400:
                    await response.aread()
                    if response.status_code in (404, 415):
                        return None
                    raise status_error(response, self._key, self._token)
                if not is_sse(response):
                    await response.aread()
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
                    # A stream asked without alt=sse comes as a JSON array of the chunks.
                    return _joined(data) if isinstance(data, list) else data
                async for event in sse(response):
                    heard = True
                    if isinstance(event.get("error"), dict):
                        detail = scrub(str(event["error"].get("message") or ""), self._key, self._token)[:160]
                        raise ProviderError(f"模型中途出错：{detail}", retryable=True)
                    last = event
                    candidate = next(iter(event.get("candidates") or []), None)
                    if not isinstance(candidate, dict):
                        continue
                    finish = candidate.get("finishReason") or finish
                    for part in (candidate.get("content") or {}).get("parts") or []:
                        if not isinstance(part, dict):
                            continue
                        _add(parts, part)
                        if part.get("text") and not part.get("thought"):
                            on_text(str(part["text"]))
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if not heard:
            raise ProviderError("模型返回的格式看不懂", retryable=False)
        if not parts and not finish:
            return last  # a refusal (promptFeedback) or something unreadable: _answer says which
        candidate = {"content": {"role": "model", "parts": parts}, "finishReason": finish}
        return {"candidates": [candidate], "usageMetadata": last.get("usageMetadata") or {}}

    async def aclose(self) -> None:
        await self._http.aclose()


def _add(parts: list[dict[str, Any]], part: dict[str, Any]) -> None:
    """A streamed piece onto the turn so far: text runs into the text before it (of the same kind,
    thought or not); anything else (a function call, a signature) is a part of its own."""
    previous = parts[-1] if parts else None
    if (
        previous is not None
        and set(part) <= {"text", "thought"}
        and "text" in previous
        and set(previous) <= {"text", "thought"}
        and bool(previous.get("thought")) == bool(part.get("thought"))
    ):
        previous["text"] = str(previous["text"]) + str(part.get("text") or "")
    else:
        parts.append(dict(part))


def _joined(chunks: list[Any]) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    finish: Any = None
    usage: dict[str, Any] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        usage = chunk.get("usageMetadata") or usage
        candidate = next(iter(chunk.get("candidates") or []), None)
        if isinstance(candidate, dict):
            finish = candidate.get("finishReason") or finish
            for part in (candidate.get("content") or {}).get("parts") or []:
                if isinstance(part, dict):
                    _add(parts, part)
    candidate = {"content": {"role": "model", "parts": parts}, "finishReason": finish}
    return {"candidates": [candidate], "usageMetadata": usage}
