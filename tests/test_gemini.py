"""Gemini's own API (generateContent): Gemini API keys, Vertex AI credentials, tools, streaming."""

from __future__ import annotations

import base64
import hashlib
import json
import random
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from nest.llm import google_auth
from nest.llm.base import ChatMessage, ProviderError, Tool, ToolCall
from nest.llm.gemini import Gemini, api_base, model_name
from nest.llm.models_list import list_models
from nest.llm.runtime import build_provider
from nest.secrets import SecretStore
from nest.storage.bots import ProviderConfig

KEY = "AIza-test-key-123456"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
VERTEX = "https://aiplatform.googleapis.com/v1/projects/demo/locations/global/publishers/google"
TOKEN_URI = "http://127.0.0.1:9/token"


def answer(
    *parts: dict[str, Any], finish: str = "STOP", prompt: int = 10, written: int = 3
) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": finish}],
        "usageMetadata": {
            "promptTokenCount": prompt,
            "candidatesTokenCount": written,
            "thoughtsTokenCount": 2,
        },
    }


def gemini(handler: Any, **kwargs: Any) -> Gemini:
    options: dict[str, Any] = {"name": "g", "base_url": GEMINI_API, "model": "models/gemini-4-argon"}
    options["api_key"] = KEY
    return Gemini(**(options | kwargs), transport=httpx.MockTransport(handler))


async def test_a_request_in_geminis_own_words_and_its_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=answer({"text": "想了想", "thought": True}, {"text": "哼，"}, {"text": "才不要"})
        )

    provider = gemini(handler, temperature=0.8)
    picture = "data:image/png;base64," + base64.b64encode(b"png").decode()
    messages = [
        ChatMessage("system", "你是阿喵。"),
        ChatMessage("assistant", "喵？"),
        ChatMessage("user", "过来"),
        ChatMessage("user", "看这个", images=(picture,)),
        ChatMessage("system", "只回一句。"),
    ]
    completion = await provider.complete(messages, max_tokens=200)
    await provider.aclose()

    assert completion.text == "哼，才不要"  # the thought is not the answer
    assert (completion.prompt_tokens, completion.completion_tokens, completion.cut) == (10, 5, False)
    [request] = seen
    assert str(request.url) == f"{GEMINI_API}/models/gemini-4-argon:generateContent"
    assert request.headers["x-goog-api-key"] == KEY and "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "你是阿喵。\n\n只回一句。"}]}
    # It starts with the user, and one side's turns in a row are one turn.
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "（继续）"}]},
        {"role": "model", "parts": [{"text": "喵？"}]},
        {
            "role": "user",
            "parts": [
                {"text": "过来"},
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(b"png").decode()}},
                {"text": "看这个"},
            ],
        },
    ]
    assert body["generationConfig"] == {
        "maxOutputTokens": 200,
        "thinkingConfig": {"thinkingLevel": "low"},
        "temperature": 0.8,
    }
    assert {s["threshold"] for s in body["safetySettings"]} == {"BLOCK_NONE"}


async def test_gemini_2_thinks_to_a_budget_and_a_cut_answer_says_so() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=answer({"text": "说到一半"}, finish="MAX_TOKENS"))

    provider = gemini(handler, model="gemini-2.5-flash", effort="medium")
    completion = await provider.complete([ChatMessage("user", "嗨")], max_tokens=50)
    await provider.aclose()
    assert completion.cut is True
    assert bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 8192}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}},
            "Gemini 拒绝回答这段对话（PROHIBITED_CONTENT）",
        ),
        ({"candidates": [{"finishReason": "SAFETY"}]}, "Gemini 没有回答（SAFETY）"),
        ({"nothing": 1}, "模型返回的格式看不懂"),
    ],
)
async def test_a_refusal_is_told(data: dict[str, Any], message: str) -> None:
    provider = gemini(lambda request: httpx.Response(200, json=data))
    with pytest.raises(ProviderError) as caught:
        await provider.complete([ChatMessage("user", "嗨")], max_tokens=50)
    await provider.aclose()
    assert str(caught.value) == message and not caught.value.retryable


async def test_errors_never_leak_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": f"quota for {KEY} exhausted"}})

    provider = gemini(handler)
    with pytest.raises(ProviderError) as caught:
        await provider.complete([ChatMessage("user", "嗨")], max_tokens=50)
    await provider.aclose()
    assert caught.value.retryable and KEY not in str(caught.value)


async def test_tools_go_as_function_declarations_and_the_models_turn_goes_back_as_it_came() -> None:
    bodies: list[dict[str, Any]] = []
    call = {"functionCall": {"name": "look", "args": {"q": "猫"}}, "thoughtSignature": "sig=="}

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=answer(call) if len(bodies) == 1 else answer({"text": "看到了"}))

    ran: list[tuple[str, dict[str, Any]]] = []

    async def run(name: str, given: dict[str, Any]) -> str:
        ran.append((name, given))
        return "一只猫"

    provider = gemini(handler)
    tool = Tool("look", "看看", {"type": "object", "properties": {"q": {"type": "string"}}})
    completion = await provider.complete([ChatMessage("user", "看看")], max_tokens=100, tools=[tool], run=run)
    await provider.aclose()

    assert completion.text == "看到了" and completion.tools_used == (("look", '{"q": "猫"}'),)
    assert ran == [("look", {"q": "猫"})]
    declared = bodies[0]["tools"][0]["functionDeclarations"][0]
    assert declared == {"name": "look", "description": "看看", "parametersJsonSchema": dict(tool.parameters)}
    assert bodies[1]["contents"][1:] == [
        {"role": "model", "parts": [call]},
        {"role": "user", "parts": [{"functionResponse": {"name": "look", "response": {"result": "一只猫"}}}]},
    ]
    assert completion.prompt_tokens == 20


async def test_a_round_with_tools_hands_back_its_state() -> None:
    bodies: list[dict[str, Any]] = []
    call = {"functionCall": {"id": "c1", "name": "look", "args": {}}}

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=answer(call) if len(bodies) == 1 else answer({"text": "完成"}))

    provider = gemini(handler)
    tools = [Tool("look", "看看")]
    first = await provider.converse(
        [ChatMessage("system", "做事"), ChatMessage("user", "开始")], tools=tools, max_tokens=99
    )
    assert first.calls == (ToolCall("c1", "look", {}),)
    second = await provider.converse(
        [], tools=tools, max_tokens=99, state=first.state, results=[(first.calls[0], "好")], words_only=True
    )
    await provider.aclose()
    assert second.text == "完成" and second.calls == ()
    assert bodies[1]["systemInstruction"] == {"parts": [{"text": "做事"}]}
    assert bodies[1]["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}
    assert bodies[1]["contents"][-1] == {
        "role": "user",
        "parts": [{"functionResponse": {"name": "look", "response": {"result": "好"}, "id": "c1"}}],
    }


async def test_streamed_text_is_told_as_it_comes() -> None:
    chunks = [
        answer({"text": "嗯", "thought": True}),
        answer({"text": "哼"}),
        answer({"text": "，不给摸"}, prompt=12, written=4),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":streamGenerateContent") and request.url.params["alt"] == "sse"
        text = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\r\n\r\n" for c in chunks)
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    pieces: list[str | None] = []
    provider = gemini(handler)
    completion = await provider.complete([ChatMessage("user", "摸摸")], max_tokens=50, on_text=pieces.append)
    await provider.aclose()
    assert pieces == ["哼", "，不给摸"] and completion.text == "哼，不给摸"
    assert (completion.prompt_tokens, completion.completion_tokens) == (12, 6)


def test_addresses_and_model_names() -> None:
    assert model_name("models/gemini-4-argon") == "gemini-4-argon"
    assert model_name("google/gemini-4-argon") == "gemini-4-argon"
    assert api_base(VERTEX.replace("publishers/google", "endpoints/openapi")) == VERTEX
    assert api_base("https://aiplatform.googleapis.com", "demo") == VERTEX
    regional = "https://us-central1-aiplatform.googleapis.com/v1/projects/demo/locations/us-central1/publishers/google"
    assert api_base("https://us-central1-aiplatform.googleapis.com/", "demo") == regional
    assert api_base(VERTEX, "other") == VERTEX
    assert api_base(GEMINI_API + "/", "demo") == GEMINI_API


# -- Vertex AI with a service account ------------------------------------------------------------------------


def _prime(bits: int, rng: random.Random) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if all(candidate % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31)) and _probably_prime(
            candidate, rng
        ):
            return candidate


def _probably_prime(n: int, rng: random.Random) -> bool:
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    for _ in range(20):
        x = pow(rng.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _der(tag: int, body: bytes) -> bytes:
    size = len(body)
    length = (
        bytes([size])
        if size < 0x80
        else bytes([0x80 | ((size.bit_length() + 7) // 8)])
        + size.to_bytes((size.bit_length() + 7) // 8, "big")
    )
    return bytes([tag]) + length + body


def _int(value: int) -> bytes:
    return _der(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def rsa_pem(seed: int = 7) -> tuple[str, int, int]:
    """A PKCS #8 PEM of a fresh 1024-bit RSA key, and its (n, e)."""
    rng = random.Random(seed)
    e = 65537
    while True:
        p, q = _prime(512, rng), _prime(512, rng)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            break
    n, d = p * q, pow(e, -1, phi)
    numbers = [0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p)]
    pkcs1 = _der(0x30, b"".join(_int(v) for v in numbers))
    algorithm = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010101")) + b"\x05\x00")
    pkcs8 = _der(0x30, _int(0) + algorithm + _der(0x04, pkcs1))
    lines = base64.encodebytes(pkcs8).decode().strip()
    return f"-----BEGIN PRIVATE KEY-----\n{lines}\n-----END PRIVATE KEY-----\n", n, e


def service_account(pem: str) -> str:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "demo",
            "private_key_id": "abc",
            "private_key": pem,
            "client_email": "bot@demo.iam.gserviceaccount.com",
            "client_id": "1",
            "token_uri": TOKEN_URI,
        },
        indent=2,
    )


def test_an_rs256_signature_checks_out() -> None:
    pem, n, e = rsa_pem()
    signature = google_auth.sign(pem, b"hello")
    size = (n.bit_length() + 7) // 8
    recovered = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    digest = google_auth.SHA256_INFO + hashlib.sha256(b"hello").digest()
    assert recovered == b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest


async def test_vertex_with_a_service_account_trades_a_signed_jwt_for_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(google_auth, "_tokens", {})
    pem, n, e = rsa_pem()
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if str(request.url) == TOKEN_URI:
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            head, claims, signature = form["assertion"][0].split(".")
            pad = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))  # noqa: E731
            assert json.loads(pad(head)) == {"alg": "RS256", "typ": "JWT"}
            told = json.loads(pad(claims))
            assert told["iss"] == "bot@demo.iam.gserviceaccount.com" and told["aud"] == TOKEN_URI
            assert told["exp"] - told["iat"] == 3600
            size = (n.bit_length() + 7) // 8
            recovered = pow(int.from_bytes(pad(signature), "big"), e, n).to_bytes(size, "big")
            assert recovered.endswith(hashlib.sha256(f"{head}.{claims}".encode()).digest())
            return httpx.Response(200, json={"access_token": "ya29.token", "expires_in": 3599})
        assert request.headers["authorization"] == "Bearer ya29.token"
        assert "x-goog-api-key" not in request.headers
        return httpx.Response(200, json=answer({"text": "在"}))

    # The bare host is enough: the credential names the project. Kept as the key field keeps it.
    provider = gemini(
        handler,
        base_url="https://aiplatform.googleapis.com",
        api_key=google_auth.compact(service_account(pem)),
    )
    for _ in range(2):
        assert (await provider.complete([ChatMessage("user", "在吗")], max_tokens=20)).text == "在"
    await provider.aclose()
    # One token, used for both calls.
    assert [str(r.url) for r in asked] == [
        TOKEN_URI,
        f"{VERTEX}/models/gemini-4-argon:generateContent",
        f"{VERTEX}/models/gemini-4-argon:generateContent",
    ]


async def test_a_refused_credential_is_told_without_its_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google_auth, "_tokens", {})
    user = {
        "type": "authorized_user",
        "client_id": "cid",
        "client_secret": "csecret-123",
        "refresh_token": "1//refresh-456",
        "quota_project_id": "demo",
        "token_uri": TOKEN_URI,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["1//refresh-456"]
        return httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "token 1//refresh-456 expired"}
        )

    provider = gemini(handler, base_url=VERTEX, api_key=json.dumps(user))
    with pytest.raises(ProviderError) as caught:
        await provider.complete([ChatMessage("user", "在吗")], max_tokens=20)
    await provider.aclose()
    assert "gcloud auth application-default login" in str(caught.value)
    assert "1//refresh-456" not in str(caught.value) and not caught.value.retryable


def test_credentials_are_read_compacted_and_survive_the_key_store(tmp_path: Any) -> None:
    pem, _, _ = rsa_pem()
    pasted = service_account(pem)
    stored = google_auth.compact(pasted)
    assert "\n" not in stored and stored.isascii() and "private_key_id" not in stored
    store = SecretStore(tmp_path / ".env")
    store.set("NEST_KEY_G", stored)
    store.reload()
    found = google_auth.credential(store.get("NEST_KEY_G"))
    assert found is not None and found.kind == "service_account" and found.project == "demo"
    assert found.private_key == pem and "PRIVATE" not in repr(found)
    # Real newlines in the key (a file opened and copied by hand) are read too.
    assert google_auth.credential(pasted.replace("\\n", "\n")) is not None
    assert google_auth.credential(KEY) is None
    for bad in ('{"type": "service_account"}', "{not json", '{"type": "other"}'):
        with pytest.raises(ValueError):
            google_auth.credential(bad)
    broken = pasted.replace("MII", "!!!")
    with pytest.raises(ValueError, match="私钥读不出来"):
        google_auth.credential(broken)


def test_build_provider_makes_gemini(tmp_path: Any) -> None:
    store = SecretStore(tmp_path / ".env")
    store.set("NEST_KEY_G", KEY)
    config = ProviderConfig("g", "gemini", GEMINI_API, "gemini-4-argon", "NEST_KEY_G", {"effort": "high"})
    provider = build_provider(config, store)
    assert isinstance(provider, Gemini) and provider.model == "gemini-4-argon"


async def test_listing_geminis_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == KEY
        models = [
            {"name": "models/gemini-4-argon", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]},
        ]
        return httpx.Response(200, json={"models": models})

    found = await list_models("gemini", GEMINI_API, KEY, transport=httpx.MockTransport(handler))
    assert found == ["gemini-4-argon"]
    with pytest.raises(ProviderError, match="Vertex AI 不提供模型列表"):
        await list_models("gemini", VERTEX, KEY, transport=httpx.MockTransport(handler))
