"""understand -> retrieve (tool) -> decide (branch) -> draft (streamed through a claim gate) -> check (redraft loop).

Every request carries a first-token deadline. Each step asks "can I still make it?" and degrades instead of waiting.
"""
import asyncio
import json
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

import library
from llm import Limiter, RateLimited, Upstream, chat

DRAFT, SMALL = "openai/gpt-oss-120b", "openai/gpt-oss-20b"
FIRST_TOKEN_BUDGET = 6.0  # s. Assumed users abandon at ~8s with nothing on screen; 2s margin for the draft to start.
PRESSURE = 8              # requests in flight; above this we skip optional model calls (smoke burst: understand ate the deadline)
REFUSAL = "The three papers don't answer this, so I won't guess."
CITE = re.compile(r"\[(C\d+)\]")
CLAIM = re.compile(r"(.*?(?:\[C\d+\])+[.,;:!?]?)", re.S)  # a claim = text up to and including its citation group
NUM = re.compile(r"\d+(?:[.,]\d+)?")
WORD = re.compile(r"[a-z]{4,}")

UNDERSTAND = ("You route questions for an assistant that answers ONLY from three papers: " + "; ".join(library.PAPERS.values())
              + '. Reply JSON {"in_scope": bool, "query": str}. in_scope=false only if the question is clearly unrelated to '
              "these papers' topics. query = search keywords for the papers.")
RERANK = 'Pick the passages that best answer the question. Reply JSON {"ids": [ids, best first]}. At most %d ids.'
ANSWER = ("Answer ONLY from the passages. End every sentence with the id of the passage that supports it, like [C12]. "
          "Copy numbers exactly. Be brief (max 4 sentences). If the passages do not answer the question, reply exactly NOT_IN_PAPERS.")


class NoCapacity(Exception):
    """No model can start in time (limits or failures): caller degrades to an extractive answer."""


@dataclass(frozen=True)
class Setting:
    size: int
    k: int
    rerank: bool

    @property
    def name(self) -> str:
        return f"{self.size}w-k{self.k}-{'rerank' if self.rerank else 'bm25'}"


@dataclass
class System:
    base: str
    source: str
    client: httpx.AsyncClient
    limiters: dict = field(default_factory=lambda: {DRAFT: Limiter(), SMALL: Limiter()})
    naive_retries: bool = False  # only for the "retries made it worse" experiment
    waiting: int = 0
    active: int = 0
    sent: deque = field(default_factory=deque)
    retried: deque = field(default_factory=deque)
    calls: list = field(default_factory=list)  # every model call, for calibrating the stand-in

    def may_retry(self) -> bool:
        """Retry budget: retries stay under 10% of calls in the last minute, so failures can't multiply load."""
        now = time.monotonic()
        for d in (self.sent, self.retried):
            while d and now - d[0] > 60:
                d.popleft()
        return self.naive_retries or len(self.retried) < 1 + 0.1 * len(self.sent)


def ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def call(sys: System, rec: dict, models: list, messages: list, max_tokens: int, deadline: float, gate=None, **kw):
    """One model call with local limiting, retry policy and model fallback. Splits time into wait/work/retry."""
    est = sum(len(m["content"]) for m in messages) // 4 + max_tokens
    for model in models:
        lim, attempts = sys.limiters[model], 0
        while attempts <= (3 if sys.naive_retries else 1):
            t = time.monotonic()
            sys.waiting += 1
            try:
                ok = await lim.acquire(est, float("inf") if sys.naive_retries else deadline)
            finally:
                sys.waiting -= 1
            rec["wait_ms"] += ms(t)
            if not ok:
                break  # this model can't start in time: try the next one
            t = time.monotonic()
            sys.sent.append(t)
            try:
                r = await chat(sys.client, sys.base, model, messages, max_tokens, on_token=gate and gate.feed, **kw)
                lim.observe(r.headers, r.tokens_in + r.tokens_out)
                rec["work_ms"] += ms(t)
                sys.calls.append(dict(model=model, status="ok", ttft_ms=int(r.ttft * 1000), total_ms=ms(t), tokens_out=r.tokens_out))
                rec["tokens_in"] += r.tokens_in
                rec["tokens_out"] += r.tokens_out
                rec["model"] = model
                return r
            except RateLimited as e:
                rec["retry_ms"] += ms(t)
                sys.calls.append(dict(model=model, status="rate_limited", total_ms=ms(t), retry_after=e.retry_after, msg=str(e)))
                if e.daily:
                    lim.exhausted = True
                    break
                lim.blocked_until = time.monotonic() + e.retry_after  # acquire() then waits only if the deadline allows
            except Upstream as e:
                rec["retry_ms"] += ms(t)
                sys.calls.append(dict(model=model, status="error", total_ms=ms(t), msg=str(e)))
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError  # the person left while httpx's own timeout fired; it swallowed our cancel
                if rec["first_token_ms"] is not None or not sys.may_retry():
                    raise  # never retry after the user saw text: they would get it twice
                if gate:
                    gate.buf = ""  # drop the failed attempt's unreleased partial text
                await asyncio.sleep(1.0 if sys.naive_retries else random.uniform(0.1, 0.5))
            attempts += 1
            rec["retries"] += 1
            sys.retried.append(time.monotonic())
    raise NoCapacity(models)


def supported(claim: str, chunks: list) -> bool:
    """Cheap check: every number in the claim appears in a cited passage, and most of its words do too."""
    source = " ".join(c.text for c in chunks).lower()
    body = CITE.sub("", claim).lower()
    words = set(WORD.findall(body))
    return all(n in source for n in NUM.findall(body)) and len(words & set(WORD.findall(source))) >= 0.35 * len(words)  # 0.5 withheld a correct paraphrase on a real run


class Gate:
    """Releases a drafted claim to the user only once its citations exist and support it."""

    def __init__(self, passages: list, emit):
        self.by_id, self.emit, self.buf, self.released, self.withheld, self.refused = {c.cid: c for c in passages}, emit, "", 0, 0, False

    def feed(self, delta: str) -> None:
        self.buf += delta.replace("【", "[").replace("】", "]")  # gpt-oss cites with full-width brackets despite the prompt
        if "NOT_IN_PAPERS" in self.buf:
            self.refused = True
        while not self.refused and (m := CLAIM.match(self.buf)):
            claim, self.buf = m.group(1), self.buf[m.end():]
            cited = [self.by_id.get(i) for i in CITE.findall(claim)]
            if all(cited) and supported(claim, cited):
                self.released += 1
                self.emit(CITE.sub(lambda x: f" ({self.by_id[x.group(1)].cite()})", claim))
            else:
                self.withheld += 1


def extractive(passages: list, daily: bool) -> str:
    why = "today's model budget is used up (it refills gradually)" if daily else "we're at our per-minute model limit"
    quotes = "\n".join(f'- "{" ".join(c.text.split()[:45])}..." ({c.cite()})' for c in passages[:2])
    return f"I can't write a full answer right now: {why}. The most relevant passages, quoted directly, are:\n{quotes}"


async def answer(sys: System, q: str, setting: Setting, rec: dict) -> str:
    t0 = time.monotonic()
    deadline = t0 + FIRST_TOKEN_BUDGET
    out = []

    def emit(s: str) -> None:
        if rec["first_token_ms"] is None:
            rec["first_token_ms"] = ms(t0)
            rec["first_token"].set()
        out.append(s)

    pressure = sys.active > PRESSURE
    sys.active += 1
    try:
        return await _answer(sys, q, setting, rec, emit, out, deadline, pressure)
    finally:
        sys.active -= 1


async def _answer(sys: System, q: str, setting: Setting, rec: dict, emit, out: list, deadline: float, pressure: bool) -> str:
    rec["degraded"] = "pressure" if pressure else ""

    # 1. understand: optional, so skipped under pressure or when the small model is unavailable
    query, in_scope = q, True
    if not pressure:
        try:
            r = await call(sys, rec, [SMALL], [{"role": "system", "content": UNDERSTAND}, {"role": "user", "content": q}],
                           150, deadline, json_mode=True)
            d = json.loads(r.text)
            in_scope, query = d.get("in_scope", True) is not False, f"{q} {d.get('query', '')}"
        except (NoCapacity, Upstream, ValueError):
            pass  # the raw question is a fine search query

    # 2. retrieve (tool call, with its own timeout)
    k = setting.k if not pressure else max(2, setting.k // 2)
    hits = await asyncio.wait_for(asyncio.to_thread(library.search, query, k * 4 if setting.rerank else k, setting.size), 2.0)
    passages = [c for _, c in hits]
    if setting.rerank and not pressure and len(passages) > k:
        try:
            listing = "\n".join(f"[{c.cid}] {' '.join(c.text.split()[:60])}" for c in passages)
            r = await call(sys, rec, [SMALL], [{"role": "system", "content": RERANK % k},
                                               {"role": "user", "content": f"Question: {q}\n\n{listing}"}], 150, deadline, json_mode=True)
            ids = json.loads(r.text).get("ids", [])
            passages = [c for i in ids for c in passages if c.cid == i][:k] or passages[:k]
        except (NoCapacity, Upstream, ValueError):
            passages = passages[:k]
    passages = passages[:k]

    # 3. decide
    if not in_scope:
        emit(REFUSAL)
        return REFUSAL
    context = "\n\n".join(f"[{c.cid}] ({c.cite()}) {c.text}" for c in passages)
    msgs = [{"role": "system", "content": ANSWER}, {"role": "user", "content": f"Passages:\n{context}\n\nQuestion: {q}"}]

    # 4. draft + 5. check: one redraft if nothing survived the gate and there is still time
    for attempt in range(2):
        gate = Gate(passages, emit)
        try:
            await call(sys, rec, [DRAFT, SMALL], msgs, 400, deadline, gate=gate)
        except NoCapacity:
            if out:
                break
            rec["status"] = "rate_limited"
            emit(extractive(passages, all(lim.exhausted for lim in sys.limiters.values())))
            return out[-1]
        if gate.released or gate.refused or time.monotonic() > deadline - 2:
            break
        msgs.append({"role": "user", "content": "Your answer had no supported, cited sentence. Every sentence must end with [C#] "
                                                "and copy numbers exactly from that passage, or reply NOT_IN_PAPERS."})
    rec["withheld"] = gate.withheld
    if rec["model"] == SMALL:
        rec["degraded"] = "small_model"
    if not gate.released:
        emit(REFUSAL)
    return " ".join(out)
