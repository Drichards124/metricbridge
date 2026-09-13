"""Lexical search over the metric catalog — BM25, in about forty lines, with no model.

**Why not embeddings in v0.** A vector index adds a model dependency, a warm-up cost, and
non-determinism to the one step an agent takes before every query. BM25 over names,
descriptions and synonyms is deterministic, instant, testable, and good enough when the
corpus is hundreds of curated metrics rather than millions of documents. The seam is
`search()`; a semantic re-ranker slots behind it when the catalog outgrows this.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .manifest import Metric, SemanticManifest

_WORD = re.compile(r"[a-z0-9]+")
K1 = 1.5
B = 0.75


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class MetricIndex:
    def __init__(self, manifest: SemanticManifest) -> None:
        self.manifest = manifest
        self._docs: dict[str, Counter] = {}
        self._lengths: dict[str, int] = {}
        for name, metric in manifest.metrics.items():
            tokens = _tokens(metric.search_text())
            self._docs[name] = Counter(tokens)
            self._lengths[name] = len(tokens) or 1
        self._avg_len = (sum(self._lengths.values()) / len(self._lengths)) if self._lengths else 1.0
        self._doc_freq = Counter(t for doc in self._docs.values() for t in doc)

    def search(self, query: str, *, domain: str | None = None, certified_only: bool = True,
               limit: int = 5) -> list[tuple[Metric, float]]:
        terms = _tokens(query)
        total = len(self._docs) or 1
        scored: list[tuple[Metric, float]] = []
        for name, doc in self._docs.items():
            metric = self.manifest.metrics[name]
            if domain and metric.domain != domain:
                continue
            if certified_only and metric.tier != "certified":
                continue
            score = 0.0
            for term in terms:
                if term not in doc:
                    continue
                idf = math.log(1 + (total - self._doc_freq[term] + 0.5) / (self._doc_freq[term] + 0.5))
                freq = doc[term]
                norm = freq * (K1 + 1) / (
                    freq + K1 * (1 - B + B * self._lengths[name] / self._avg_len))
                score += idf * norm
            if score > 0:
                scored.append((metric, round(score, 4)))
        scored.sort(key=lambda pair: (-pair[1], pair[0].name))
        return scored[:limit]
