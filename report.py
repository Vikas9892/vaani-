"""Recompute every table in the write-up from results.csv: python report.py > tables.md"""
import csv
from datetime import datetime

import numpy as np

# shortcut: Groq on-demand list prices in $/1M tokens (in, out) used for the cost column; update if Groq changes them.
PRICE = {"openai/gpt-oss-120b": (0.15, 0.60), "openai/gpt-oss-20b": (0.075, 0.30), "": (0.15, 0.60)}

rows = list(csv.DictReader(open("results.csv", encoding="utf-8")))


def pct(xs, *qs) -> str:
    return " / ".join(str(int(np.percentile(xs, q))) for q in qs) if xs else "n/a"


def share(rs, cond) -> str:
    return f"{100 * sum(map(cond, rs)) / len(rs):.1f}"


def groups(key):
    out = {}
    for r in rows:
        if not r["scenario"].startswith("probe"):
            out.setdefault(key(r), []).append(r)
    return out


def latency(rs):
    ft = [int(r["first_token_ms"]) for r in rs if r["first_token_ms"]]  # requests that got any reply
    tot = [int(r["total_ms"]) for r in rs if r["first_token_ms"]]
    return ft, tot


print("Percentiles are over requests that received a first token; given-up requests count in 'Timed out or given up'.\n")
print("### Table 2a\n\n| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |\n|---|---|---|---|---|---|---|")
by_scn = groups(lambda r: (r["scenario"], r["source"]))
for (scn, src), rs in by_scn.items():
    starts = [datetime.fromisoformat(r["start_time"]).timestamp() for r in rs]
    dur = max(s + int(r["total_ms"]) / 1000 for s, r in zip(starts, rs)) - min(starts)
    span = max(starts) - min(starts)
    rate = f"{len(rs) / span * 60:.0f}" if span > 1 else f"{len(rs)} at once"
    ft, tot = latency(rs)
    print(f"| {scn} | {src} | {len(rs)} | {dur:.0f} | {rate} | {pct(ft, 50, 95, 99)} | {pct(tot, 50, 95, 99)} |")

print("\n### Table 2b\n\n| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |\n|---|---|---|---|---|---|---|---|")
for (scn, src), rs in by_scn.items():
    ok = [r for r in rs if r["status"] == "ok"]
    tok = np.mean([int(r["tokens_in"]) + int(r["tokens_out"]) for r in ok]) if ok else 0
    cost = np.mean([(int(r["tokens_in"]) * PRICE[r["model"]][0] + int(r["tokens_out"]) * PRICE[r["model"]][1]) / 1e6 for r in ok]) * 1000 if ok else 0
    print(f"| {scn} | {share(rs, lambda r: r['status'] == 'ok')} | {share(rs, lambda r: r['status'] == 'rate_limited')} | "
          f"{share(rs, lambda r: r['status'] == 'timeout')} | {np.mean([int(r['retries']) for r in rs]):.2f} | "
          f"{max(int(r['queue_len'] or 0) for r in rs)} | {tok:.0f} | ${cost:.2f} |")

print("\n### Table 3 (real Groq, scenario real_steady)\n\n| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |\n|---|---|---|---|---|---|---|---|")
for setting, rs in groups(lambda r: r["retrieval_setting"]).items():
    rs = [r for r in rs if r["scenario"] == "real_steady"]
    if not rs:
        continue
    ft, tot = latency(rs)
    print(f"| {setting} | {len(rs)} | {share(rs, lambda r: r['answer_grade'] == 'correct')} | {share(rs, lambda r: r['answer_grade'] == 'wrong')} | "
          f"{share(rs, lambda r: r['answer_grade'] == 'refused_correctly')} | {np.mean([int(r['tokens_in']) + int(r['tokens_out']) for r in rs]):.0f} | "
          f"{pct(ft, 50, 95)} | {pct(tot, 50, 95)} |")
