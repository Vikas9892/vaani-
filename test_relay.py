import asyncio
import time

from library import Chunk
from llm import Limiter
from pipeline import Gate
from run import grade

P = [Chunk("C1", "1706.03762v7", 7, "We used warmup_steps = 4000 for the learning rate schedule.")]


def gated(text: str) -> tuple[list, Gate]:
    out = []
    g = Gate(P, out.append)
    for piece in text.split(" "):  # arrives a piece at a time, like a stream
        g.feed(piece + " ")
    return out, g


def test_gate_releases_supported_claim_with_page_citation():
    out, g = gated("The schedule used warmup_steps = 4000 [C1].")
    assert g.released == 1 and "(Attention Is All You Need, p.7)" in out[0]


def test_gate_withholds_wrong_number_unknown_id_and_uncited_text():
    out, g = gated("The schedule used warmup_steps = 8000 [C1]. Also 4000 steps [C9]. Uncited trailing claim.")
    assert out == [] and g.withheld == 2


def test_gate_accepts_fullwidth_brackets_gpt_oss_emits():
    assert gated("The schedule used 4000 warmup steps【C1】.")[1].released == 1


def test_gate_detects_refusal():
    assert gated("NOT_IN_PAPERS")[1].refused


def test_grade():
    q = {"expect": [["4000"]], "paper": "Attention"}
    assert grade(q, "4000 steps (Attention Is All You Need, p.7)") == "correct"
    assert grade(q, "8000 steps (Attention Is All You Need, p.7)") == "wrong"
    assert grade({"expect": None}, "The three papers don't answer this, so I won't guess.") == "refused_correctly"


def test_limiter_reserves_tokens_and_refuses_past_deadline():
    lim = Limiter(rpm=30, tpm=1000)
    assert asyncio.run(lim.acquire(800, time.monotonic() + 1))
    assert not asyncio.run(lim.acquire(800, time.monotonic() + 1))  # would need ~60s: say no now, don't wait


def test_grade_ignores_unicode_spacing_gpt_oss_emits():
    q = {"expect": [["3.5 days"]], "paper": "Attention"}
    assert grade(q, "took 3.5 days on English‑to‑French (Attention Is All You Need, p.1)") == "correct"


def test_cancelled_request_is_not_retried_after_upstream_timeout():
    # Regression: a cancel arriving while httpx raised its own timeout was swallowed and the call retried (4 overload rows).
    import pipeline
    from llm import Upstream

    async def go():
        sys = pipeline.System("http://x", "test", None)
        calls = []

        async def fake_chat(*a, **k):
            calls.append(1)
            asyncio.current_task().cancel()  # person gives up at 8.0s, the same moment the 8.0s read timeout fires...
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass                         # ...httpx cleanup swallows the CancelledError (cancelling() stays 1)
            raise Upstream("ReadTimeout")

        pipeline.chat = fake_chat
        rec = dict(wait_ms=0, work_ms=0, retry_ms=0, retries=0, tokens_in=0, tokens_out=0, first_token_ms=None, model="")
        task = asyncio.create_task(pipeline.call(sys, rec, [pipeline.DRAFT], [{"content": "q"}], 10, time.monotonic() + 5))
        await asyncio.gather(task, return_exceptions=True)
        return len(calls), task.cancelled()

    assert asyncio.run(go()) == (1, True)
