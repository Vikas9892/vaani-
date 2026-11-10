"""Streaming OpenAI-compatible client (Groq or the stand-in) plus a per-model client-side limiter."""
import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field

import httpx


class RateLimited(Exception):
    def __init__(self, retry_after: float, daily: bool, msg: str):
        super().__init__(msg)
        self.retry_after, self.daily = retry_after, daily


class Upstream(Exception):
    """5xx, connection failure, or a stall past the first-token timeout: the call may be retried."""


@dataclass
class Limiter:
    """Mirrors the provider's per-model limits so we wait locally instead of spending requests on 429s.

    Groq reserves prompt + max_tokens against TPM at admission (measured: 79 prompt + 200 max -> 279 debited),
    so we reserve the same way.
    """
    rpm: int = 30
    tpm: int = 8000
    rpd: int = 1000
    tpd: int = 200_000
    window: deque = field(default_factory=deque)
    used_req: int = 0
    used_tok: int = 0
    blocked_until: float = 0.0
    exhausted: bool = False

    def wait_for(self, tokens: int) -> float:
        now = time.monotonic()
        while self.window and now - self.window[0][0] >= 60:
            self.window.popleft()
        if now < self.blocked_until:
            return self.blocked_until - now
        if len(self.window) >= self.rpm or sum(t for _, t in self.window) + tokens > self.tpm:
            return 60 - (now - self.window[0][0]) + 0.01
        return 0.0

    async def acquire(self, tokens: int, deadline: float) -> bool:
        """Reserve capacity, waiting at most until `deadline`. False means: do something else, don't wait."""
        while not self.exhausted:
            w = self.wait_for(tokens)
            if w == 0:
                self.window.append((time.monotonic(), tokens))
                self.used_req += 1
                return True
            if time.monotonic() + w > deadline:
                return False
            await asyncio.sleep(w)
        return False

    def observe(self, headers: httpx.Headers, tokens_used: int) -> None:
        # Limits "change without notice": trust what the server says over what we were configured with.
        self.tpm = int(headers.get("x-ratelimit-limit-tokens", self.tpm))
        self.rpd = int(headers.get("x-ratelimit-limit-requests", self.rpd))
        self.used_tok += tokens_used
        if headers.get("x-ratelimit-remaining-requests") == "0" or self.used_tok >= self.tpd or self.used_req >= self.rpd:
            self.exhausted = True


@dataclass
class Reply:
    text: str
    ttft: float  # seconds from send to first content token
    tokens_in: int
    tokens_out: int
    headers: httpx.Headers


async def chat(client: httpx.AsyncClient, base: str, model: str, messages: list, max_tokens: int,
               on_token=None, json_mode: bool = False, first_token_timeout: float = 8.0, total_timeout: float = 30.0) -> Reply:
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}}
    if model.startswith("openai/gpt-oss"):
        body.update(reasoning_effort="low", include_reasoning=False)
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    t0, parts, ttft, usage = time.perf_counter(), [], None, {}

    async def run():
        nonlocal ttft, usage
        # read timeout = max silence between bytes; catches calls that hang before the first token
        async with client.stream("POST", f"{base}/chat/completions", json=body,
                                 timeout=httpx.Timeout(total_timeout, read=first_token_timeout)) as r:
            if r.status_code == 429:
                msg = (await r.aread()).decode()
                raise RateLimited(float(r.headers.get("retry-after", 2)), "per day" in msg, msg[:200])
            if r.status_code >= 500:
                raise Upstream(f"{r.status_code}")
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code} {(await r.aread()).decode()[:300]}")
            async for line in r.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[6:])
                usage = chunk.get("usage") or chunk.get("x_groq", {}).get("usage") or usage
                delta = chunk["choices"][0]["delta"].get("content") if chunk.get("choices") else None
                if delta:
                    ttft = ttft or time.perf_counter() - t0
                    parts.append(delta)
                    if on_token:
                        on_token(delta)
            return r.headers

    try:
        headers = await asyncio.wait_for(run(), total_timeout)
    except (httpx.TransportError, asyncio.TimeoutError) as e:
        raise Upstream(type(e).__name__) from e
    return Reply("".join(parts), ttft or time.perf_counter() - t0, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), headers)
