"""Run experiments; every request becomes one row of results.csv.

python run.py probe                  real Groq: RPM, TPM, in-flight, 429 body, recovery time
python run.py quality 250 4 0        real Groq: all questions at one retrieval setting (size, k, rerank)
python run.py load <scenario>        see SCENARIOS (stand-in unless the name starts with real_)
python run.py calibrate              fit the stand-in to real calls in calls.jsonl
"""
import asyncio
import csv
import json
import math
import os
import random
import socket
import statistics
import subprocess
import sys as _sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx

import library
from llm import Limiter, RateLimited, Upstream, chat
from pipeline import DRAFT, REFUSAL, SMALL, Setting, System, answer, ms

HERE = Path(__file__).parent
COLS = ("request_id scenario source start_time first_token_ms total_ms status retries tokens_in tokens_out retrieval_setting "
        "answer_grade queue_len wait_ms work_ms retry_ms model degraded withheld").split()
QUESTIONS = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
GROQ, STANDIN = "https://api.groq.com/openai/v1", "http://127.0.0.1:8900/v1"
PATIENCE = 8.0  # s without a first token before the simulated person gives up
DEFAULT = Setting(120, 4, False)  # best on Table 3: most correct, fewest tokens
# Stand-in runs at 10x the free-tier per-minute limits (scale), so minutes of load give hundreds of requests.
SCENARIOS = {
    "steady": dict(rate=20, duration=180),
    "burst": dict(burst=100),
    "overload": dict(rate=120, duration=180),
    "overload_naive_retry": dict(rate=120, duration=180, naive=True, fail=0.05),
    "overload_budget_retry": dict(rate=120, duration=180, fail=0.05),
    "halved_limit": dict(rate=20, duration=180, scale=5, believed_scale=10),  # limit cut without notice
    "daily_exhausted": dict(rate=20, duration=120, rpd=15),
    "real_burst": dict(burst=8),
}


def write(path: str, rows: list) -> None:
    new = not (HERE / path).exists()
    with open(HERE / path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def norm(s: str) -> str:
    # gpt-oss writes "3.5 days" and "English‑to‑French"; NFKC + hyphen fold makes substring checks fair
    return unicodedata.normalize("NFKC", s).replace("‐", "-").lower()


def grade(q: dict, text: str) -> str:
    text = norm(text)
    refused = text.strip() == norm(REFUSAL) or text.startswith("i can't write")
    if q["expect"] is None:
        return "refused_correctly" if refused else "wrong"
    if refused:
        return "refused_wrongly"
    hit = all(any(norm(a) in text for a in group) for group in q["expect"])
    return "correct" if hit and norm(q["paper"]) in text else "wrong"


async def one(sys: System, q: dict, setting: Setting, scenario: str, rid: str, graded: bool) -> dict:
    rec = dict(request_id=rid, scenario=scenario, source=sys.source, start_time=now_iso(), first_token_ms=None, status="ok",
               retries=0, tokens_in=0, tokens_out=0, retrieval_setting=setting.name, answer_grade="", queue_len=sys.active,
               wait_ms=0, work_ms=0, retry_ms=0, model="", degraded="", withheld=0, first_token=asyncio.Event())
    t0, text = time.monotonic(), ""
    task = asyncio.create_task(answer(sys, q["q"], setting, rec))
    seen = asyncio.create_task(rec["first_token"].wait())
    await asyncio.wait({task, seen}, timeout=PATIENCE, return_when=asyncio.FIRST_COMPLETED)
    seen.cancel()
    if rec["first_token_ms"] is None and not task.done():
        task.cancel()  # the person left; stop spending budget on them
        await asyncio.gather(task, return_exceptions=True)
        rec["status"] = "timeout"
    else:
        try:
            text = await asyncio.wait_for(task, 60)
        except asyncio.TimeoutError:
            rec["status"] = "timeout"
        except Exception as e:  # recorded per row, never hidden
            rec["status"], text = "error", repr(e)
    rec["total_ms"] = ms(t0)
    if graded and rec["status"] != "error":
        rec["answer_grade"] = grade(q, text) if rec["status"] != "timeout" else ("refused_correctly" if q["expect"] is None else "refused_wrongly")
    with open(HERE / "answers.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"request_id": rid, "q": q["q"], "answer": text, "grade": rec["answer_grade"]}) + "\n")
    return rec


def client(base: str) -> httpx.AsyncClient:
    key = os.environ["GROQ_API_KEY"] if base == GROQ else "standin"
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, limits=httpx.Limits(max_connections=500))


async def run_requests(name: str, base: str, source: str, setting: Setting, questions: list, arrivals: list,
                       graded: bool, limiters: dict | None = None, naive: bool = False) -> list:
    async with client(base) as c:
        sys = System(base, source, c, naive_retries=naive, **({"limiters": limiters} if limiters else {}))
        library.search("warm", 1, setting.size)  # build the index before traffic: a cold build timed out 4/8 of a real burst
        t0, tasks = time.monotonic(), []
        for i, (at, q) in enumerate(zip(arrivals, questions)):
            await asyncio.sleep(max(0.0, t0 + at - time.monotonic()))
            tasks.append(asyncio.create_task(one(sys, q, setting, name, f"{name}-{setting.name}-{int(t0)}-{i:04d}", graded)))
        rows = await asyncio.gather(*tasks)
        if source == "real":
            with open(HERE / "calls.jsonl", "a") as f:
                f.writelines(json.dumps(x) + "\n" for x in sys.calls)
    write("results.csv", rows)
    return rows


async def quality(size: int, k: int, rerank: int) -> None:
    setting = Setting(size, k, bool(rerank))
    # One question every 20s: ~2.5K tokens reserved per answer vs 8K TPM is the free tier's sustainable rate.
    arrivals = [i * 20.0 for i in range(len(QUESTIONS))]
    rows = await run_requests("real_steady", GROQ, "real", setting, QUESTIONS, arrivals, graded=True)
    print(setting.name, {g: sum(r["answer_grade"] == g for r in rows) for g in ("correct", "wrong", "refused_correctly", "refused_wrongly")})


async def load(name: str) -> None:
    s = SCENARIOS[name]
    real = name.startswith("real_")
    rng = random.Random(7)
    if "burst" in s:
        arrivals = [0.0] * s["burst"]
    else:  # Poisson arrivals at `rate` per minute
        arrivals, t = [], rng.expovariate(s["rate"] / 60)
        while t < s["duration"]:
            arrivals.append(t)
            t += rng.expovariate(s["rate"] / 60)
    questions = [rng.choice(QUESTIONS) for _ in arrivals]
    proc = None
    if not real:
        args = ["--scale", str(s.get("scale", 10)), "--rpd", str(s.get("rpd", 1000))] + (["--fail", str(s["fail"])] if "fail" in s else [])
        proc = subprocess.Popen([_sys.executable, str(HERE / "standin.py"), *args])
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", 8900), 0.5).close()
                break
            except OSError:
                time.sleep(0.2)
    believed = s.get("believed_scale", s.get("scale", 10)) if not real else 1
    limiters = {m: Limiter(rpm=int(30 * believed), tpm=int(8000 * believed)) for m in (DRAFT, SMALL)}
    try:
        rows = await run_requests(name, GROQ if real else STANDIN, "real" if real else "stand-in", DEFAULT, questions,
                                  arrivals, graded=real, limiters=limiters, naive=s.get("naive", False))
    finally:
        if proc:
            proc.terminate()
    print(name, len(rows), {st: sum(r["status"] == st for r in rows) for st in ("ok", "rate_limited", "timeout", "error")})


async def probe(*steps: str) -> None:
    """Find the real limits by hitting them, cheaply, on the small model. Steps: burst sustained flood (default: burst)."""
    steps = steps or ("burst",)
    rows = []
    async with client(GROQ) as c:
        async def hit(scenario: str, i: int, max_tokens: int, pad: int | None = None) -> dict:
            t0, row = time.monotonic(), dict(request_id=f"{scenario}-{i:03d}", scenario=scenario, source="real", start_time=now_iso(),
                                             retries=0, tokens_in=0, tokens_out=0, retrieval_setting="none", model=SMALL)
            try:
                r = await chat(c, GROQ, SMALL, [{"role": "user", "content": "Say OK. " + "pad " * (max_tokens // 3 if pad is None else pad)}], max_tokens)
                row.update(status="ok", first_token_ms=int(r.ttft * 1000), tokens_in=r.tokens_in, tokens_out=r.tokens_out,
                           degraded=f"remaining_tokens={r.headers.get('x-ratelimit-remaining-tokens')} remaining_requests={r.headers.get('x-ratelimit-remaining-requests')}")
            except RateLimited as e:
                row.update(status="rate_limited", degraded=f"retry_after={e.retry_after} msg={e}")
            except Upstream as e:
                row.update(status="error", degraded=str(e))
            row["total_ms"] = ms(t0)
            return row

        if "burst" in steps:
            # RPM + in-flight: 40 tiny requests at once (docs: 30 RPM). Then TPM: 12 x ~1,300 reserved vs 8,000.
            rows += await asyncio.gather(*(hit("probe_rpm", i, 1) for i in range(40)))
            await asyncio.sleep(61)
            rows += await asyncio.gather(*(hit("probe_tpm", i, 1000) for i in range(12)))
        if "sustained" in steps:
            # The burst above got 39/40 through, so test a sliding window: 1 tiny request per second for 75 s.
            for i in range(75):
                rows.append(await hit("probe_rpm_sustained", i, 1))
                await asyncio.sleep(max(0.0, 1 - rows[-1]["total_ms"] / 1000))
            await asyncio.sleep(61)
        if "flood" in steps:
            # TPM with real tokens, not reservations: 8 x ~2,700 prompt tokens in parallel ~= 21K vs 8K TPM.
            rows += await asyncio.gather(*(hit("probe_tpm_flood", i, 1, pad=2700) for i in range(8)))
            # Recovery: one tiny request per second until it succeeds.
            for i in range(90):
                rows.append(await hit("probe_recovery", i, 1))
                if rows[-1]["status"] == "ok":
                    break
                await asyncio.sleep(1)
    write("results.csv", rows)
    for r in rows:
        print(r["request_id"], r["status"], r.get("first_token_ms"), r["total_ms"], r.get("degraded", "")[:160])


def regrade() -> None:
    """Re-apply grade() to stored answers (used once after fixing the grader's unicode bug)."""
    qs = {q["q"]: q for q in QUESTIONS}
    answers = [json.loads(l) for l in (HERE / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    new = {a["request_id"]: grade(qs[a["q"]], a["answer"]) for a in answers if a["grade"] and a["answer"]}
    rows = list(csv.DictReader(open(HERE / "results.csv", encoding="utf-8")))
    for r in rows:
        r["answer_grade"] = new.get(r["request_id"], r["answer_grade"])
    (HERE / "results.csv").unlink()
    write("results.csv", rows)
    print(sum(1 for a in answers if a["request_id"] in new and new[a["request_id"]] != a["grade"]), "grades changed")


def calibrate() -> None:
    calls = [json.loads(l) for l in (HERE / "calls.jsonl").read_text().splitlines()]
    ok = [c for c in calls if c["status"] == "ok" and c["model"] == DRAFT]
    logs = [math.log(c["ttft_ms"] / 1000) for c in ok]
    med = statistics.median(c["ttft_ms"] for c in ok)
    cal = dict(ttft_mu=statistics.mean(logs), ttft_sigma=statistics.stdev(logs),
               tokens_per_s=statistics.median(c["tokens_out"] / max((c["total_ms"] - c["ttft_ms"]) / 1000, 0.01) for c in ok),
               reasoning_tokens=20, fail_rate=sum(c["status"] == "error" for c in calls) / len(calls),
               slow_rate=sum(c["ttft_ms"] > 3 * med for c in ok) / len(ok), n_calls=len(ok))
    (HERE / "calibration.json").write_text(json.dumps(cal, indent=2))
    print(cal)


if __name__ == "__main__":
    cmd, *a = _sys.argv[1:]
    if cmd in ("calibrate", "regrade"):
        globals()[cmd]()
    else:
        asyncio.run({"probe": lambda: probe(*a), "quality": lambda: quality(*map(int, a)), "load": lambda: load(*a)}[cmd]())
