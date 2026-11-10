"""Stand-in for Groq: same API, same limit headers and 429 bodies, latency calibrated from calibration.json.

python standin.py --port 8900 [--scale 10] [--rpd 1000] [--fail 0.01] [--slow 0.03]
"""
import argparse
import asyncio
import json
import math
import random
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web

CAL = json.loads((Path(__file__).parent / "calibration.json").read_text())


def fake_reply(body: dict) -> str:
    """Plausible content so the real pipeline runs end to end: JSON for router/rerank, cited extract for drafts."""
    system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
    if body.get("response_format"):
        ids = re.findall(r"\[(C\d+)\]", user)
        return json.dumps({"ids": ids[:4]} if '"ids"' in system else {"in_scope": True, "query": user})
    passages = re.findall(r"\[(C\d+)\] \([^)]*\) (\S+(?: \S+){0,24})", user)
    return " ".join(f"{text} [{cid}]." for cid, text in passages[:2]) or "NOT_IN_PAPERS"


def sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def build(a) -> web.Application:
    rpm, tpm = int(30 * a.scale), int(8000 * a.scale)
    windows, day, inflight = defaultdict(deque), defaultdict(int), [0]

    async def completions(req: web.Request) -> web.StreamResponse:
        body = await req.json()
        model, now = body["model"], time.monotonic()
        prompt = sum(len(m["content"]) for m in body["messages"]) // 4
        w = windows[model]
        while w and now - w[0][0] >= 60:
            w.popleft()
        hdr = {"x-ratelimit-limit-requests": str(a.rpd), "x-ratelimit-remaining-requests": str(max(a.rpd - day[model], 0)),
               "x-ratelimit-limit-tokens": str(tpm), "x-ratelimit-remaining-tokens": str(max(tpm - sum(t for _, t in w), 0))}

        def limited(kind: str, retry: float):
            msg = f"Rate limit reached for model `{model}` on {kind}. Please try again in {retry:.2f}s."
            return web.json_response({"error": {"message": msg, "type": "tokens", "code": "rate_limit_exceeded"}},
                                     status=429, headers={**hdr, "retry-after": str(math.ceil(retry))})

        if day[model] >= a.rpd:
            return limited("requests per day (RPD)", 86400 / a.rpd)
        cost = prompt + body.get("max_tokens", 1024)  # Groq debits max_tokens at admission (measured)
        if len(w) >= rpm or sum(t for _, t in w) + cost > tpm:
            return limited("tokens per minute (TPM)", 60 - (now - w[0][0]) if w else 1)
        w.append((now, cost))
        day[model] += 1
        if random.random() < a.fail:
            await asyncio.sleep(random.uniform(0.05, 0.5))
            return web.json_response({"error": {"message": "Service Unavailable"}}, status=503)

        inflight[0] += 1
        try:
            # lognormal TTFT fitted to real calls, stretched by our own concurrency ("slower under load")
            ttft = random.lognormvariate(CAL["ttft_mu"], CAL["ttft_sigma"]) * (1 + inflight[0] / a.capacity)
            if random.random() < a.slow:
                ttft *= random.uniform(5, 15)  # succeeds, just slowly
            text = fake_reply(body)
            resp = web.StreamResponse(headers={**hdr, "content-type": "text/event-stream"})
            await resp.prepare(req)
            await asyncio.sleep(ttft)
            pieces = re.findall(r"\S+\s*", text)
            for p in pieces:
                await resp.write(sse({"choices": [{"delta": {"content": p}}]}))
                await asyncio.sleep(1 / CAL["tokens_per_s"])
            await resp.write(sse({"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": int(len(pieces) * 1.3) + CAL["reasoning_tokens"]}}))
            await resp.write(b"data: [DONE]\n\n")
            return resp
        except ConnectionResetError:
            return resp  # the caller gave up mid-stream, as real users do
        finally:
            inflight[0] -= 1

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completions)
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--scale", type=float, default=1.0, help="multiplier on the free-tier per-minute limits")
    ap.add_argument("--rpd", type=int, default=1000)
    ap.add_argument("--fail", type=float, default=CAL["fail_rate"])
    ap.add_argument("--slow", type=float, default=CAL["slow_rate"])
    ap.add_argument("--capacity", type=float, default=20, help="concurrency at which latency doubles")
    web.run_app(build(ap.parse_args()), port=ap.parse_args().port, print=None)
