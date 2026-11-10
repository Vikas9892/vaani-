# The Relay

A cited Q&A pipeline over three arXiv papers, measured on Groq's free tier and on a stand-in model service.

| File | What it is |
|---|---|
| `library.py` | PDF -> per-page chunks (25% overlap, bibliography pages dropped) -> BM25 |
| `llm.py` | streaming OpenAI-compatible client + per-model limiter that mirrors Groq's limits |
| `pipeline.py` | understand -> retrieve (tool) -> decide -> draft through a claim gate -> check/redraft |
| `standin.py` | fake Groq: same API, headers, 429 bodies; calibrated latency, failures, load slowdown, streaming |
| `run.py` | probe / quality / load scenarios / calibrate; writes `results.csv`, `answers.jsonl`, `calls.jsonl` |
| `report.py` | recomputes every table from `results.csv` |
| `questions.json` | 22 hand-checked questions (16 answerable, 6 not answerable from the papers) |

```bash
pip install httpx aiohttp pymupdf rank-bm25 numpy pytest
export GROQ_API_KEY=...            # never committed
mkdir papers && for id in 1706.03762v7 2410.00037v1 2507.07955v2; do curl -sL -o papers/$id.pdf https://arxiv.org/pdf/$id; done
pytest -q
python run.py probe                 # real limits
python run.py quality 120 4 0       # real, one retrieval setting (also 250 4 0, 120 4 1)
python run.py calibrate             # fit stand-in to real calls
python run.py load steady           # also burst, overload, overload_naive_retry, overload_budget_retry, halved_limit, daily_exhausted, real_burst
python report.py > tables.md
```
