"""Hybrid skill search: BM25 for exact names and terms, plus a vector score for
paraphrases, merged with reciprocal rank fusion.

Vectors alone miss exact identifiers ("invoice-totals"), and BM25 alone misses
paraphrases ("add up what this bill comes to"). The default embedder is a local
character-trigram hasher so tests run offline; pass any `embed` function (an
embedding API, a sentence-transformer) to replace it.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Sequence

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i in is it its me my of on or our "
    "please so that the this to us use used using we what when which will with you your".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


def hashed_trigram_embed(text: str, dims: int = 512) -> list[float]:
    vec = [0.0] * dims
    for word in tokenize(text):
        padded = f" {word} "
        for i in range(len(padded) - 2):
            h = int.from_bytes(hashlib.blake2b(padded[i : i + 3].encode(), digest_size=4).digest(), "big")
            vec[h % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@dataclass(frozen=True)
class Doc:
    skill_id: str
    name: str
    description: str

    def text(self) -> str:
        # The name counts twice: an exact skill name is the strongest signal there is.
        spaced = self.name.replace("-", " ")
        return f"{spaced} {spaced} {self.description}"


@dataclass(frozen=True)
class Hit:
    skill_id: str
    name: str
    score: float  # fused RRF score
    bm25: float
    cosine: float


class HybridIndex:
    def __init__(self, docs: Sequence[Doc], embed: Callable[[str], list[float]] = hashed_trigram_embed,
                 k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = list(docs)
        self.embed = embed
        self.k1, self.b = k1, b
        self.tokens = [tokenize(d.text()) for d in self.docs]
        self.avg_len = sum(map(len, self.tokens)) / max(len(self.tokens), 1)
        df: Counter[str] = Counter()
        for toks in self.tokens:
            df.update(set(toks))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.vectors = [embed(d.text()) for d in self.docs]

    def _bm25(self, query: list[str], i: int) -> float:
        tf = Counter(self.tokens[i])
        length = len(self.tokens[i])
        score = 0.0
        for term in query:
            if term not in tf:
                continue
            f = tf[term]
            score += self.idf[term] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self.avg_len))
        return score

    def search(self, query: str, k: int = 5, rrf_k: int = 60) -> list[Hit]:
        if not self.docs:
            return []
        q_tokens = tokenize(query)
        q_vec = self.embed(query)
        bm25 = [self._bm25(q_tokens, i) for i in range(len(self.docs))]
        cos = [sum(a * b for a, b in zip(q_vec, v)) for v in self.vectors]
        by_bm25 = sorted(range(len(self.docs)), key=lambda i: -bm25[i])
        by_cos = sorted(range(len(self.docs)), key=lambda i: -cos[i])
        fused: dict[int, float] = {}
        for ranking in (by_bm25, by_cos):
            for rank, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1.0 / (rrf_k + rank + 1)
        order = sorted(fused, key=lambda i: -fused[i])[:k]
        return [Hit(self.docs[i].skill_id, self.docs[i].name, fused[i], bm25[i], cos[i]) for i in order]


def route(index: HybridIndex, query: str, min_bm25: float = 1.0, min_cosine: float = 0.30) -> Hit | None:
    """Pick one skill or none. 'None' matters as much as a hit: a skill that fires
    on the wrong prompt costs context and can run the wrong script."""
    hits = index.search(query, k=1)
    if not hits:
        return None
    top = hits[0]
    return top if (top.bm25 >= min_bm25 or top.cosine >= min_cosine) else None
