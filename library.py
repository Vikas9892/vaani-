"""Load the three papers, chunk them per page, and search with BM25."""
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import fitz
from rank_bm25 import BM25Okapi

PAPERS = {"1706.03762v7": "Attention Is All You Need", "2410.00037v1": "Moshi", "2507.07955v2": "Dynamic Chunking (H-Net)"}
DIR = Path(__file__).parent / "papers"
tokenize = lambda s: re.findall(r"[a-z0-9]+", s.lower())


@dataclass(frozen=True)
class Chunk:
    cid: str
    paper: str
    page: int
    text: str

    def cite(self) -> str:
        return f"{PAPERS[self.paper]}, p.{self.page}"


def is_references(text: str) -> bool:
    # Bibliography pages match a naive retriever on every topic word; both citation styles appear in the library.
    return len(re.findall(r"(?m)^\[\d+\]|\b(?:19|20)\d\d\.|URL http|arXiv preprint|Proceedings of", text)) > 10


@lru_cache
def chunks(size: int, drop_refs: bool = True) -> tuple[Chunk, ...]:
    out = []
    for pid in PAPERS:
        for pno, page in enumerate(fitz.open(DIR / f"{pid}.pdf"), 1):
            text = page.get_text()
            if drop_refs and is_references(text):
                continue
            words = text.split()
            step = max(size * 3 // 4, 1)  # 25% overlap so a fact split across a boundary survives in one chunk
            for i in range(0, max(len(words) - size // 4, 1), step):
                out.append(Chunk(f"C{len(out)}", pid, pno, " ".join(words[i:i + size])))
    return tuple(out)


@lru_cache
def index(size: int, drop_refs: bool = True) -> BM25Okapi:
    return BM25Okapi([tokenize(c.text) for c in chunks(size, drop_refs)])


def search(query: str, k: int, size: int, drop_refs: bool = True) -> list[tuple[float, Chunk]]:
    scores = index(size, drop_refs).get_scores(tokenize(query))
    top = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)[:k]
    return [(float(scores[i]), chunks(size, drop_refs)[i]) for i in top]


if __name__ == "__main__":
    assert len(chunks(200)) < len(chunks(200, drop_refs=False)), "reference filter removed nothing"
    for s, c in search("how many layers in the encoder", 3, 200):
        print(round(s, 1), c.cid, c.cite(), c.text[:100])
