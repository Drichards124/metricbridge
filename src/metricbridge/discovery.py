"""Lexical search over the catalog — BM25, no model, no warm-up.

A vector index would add a dependency, a warm-up cost and non-determinism to the one step an agent
takes before every query. BM25 over names, descriptions, synonyms and authorised cuts is instant,
deterministic, and testable with exact expected output. The seam is `rank`: a re-ranker slots
behind it when a measured recall failure says it should, not before.

The same ranking serves refusal suggestions, so "did you mean" and "what exists" agree.
"""

import difflib
import math
import re
from collections import Counter

from .manifest import Metric, SemanticManifest, dimension_catalog, metric_sources

_WORD = re.compile(r"[a-z0-9]+")
K1 = 1.5
B = 0.75


def tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("_", " "))


class Bm25:
    """Ranks documents against a query. Ties break by key, so results never wobble."""

    def __init__(self, documents: dict[str, str]) -> None:
        self._terms = {key: Counter(tokens(text)) for key, text in documents.items()}
        self._lengths = {key: sum(counts.values()) or 1 for key, counts in self._terms.items()}
        self._average = (sum(self._lengths.values()) / len(self._lengths)) if self._terms else 1.0
        self._document_frequency = Counter(
            term for counts in self._terms.values() for term in counts
        )

    def rank(
        self, query: str, *, limit: int | None = None, keys: set[str] | None = None
    ) -> list[tuple[str, float]]:
        total = len(self._terms) or 1
        scored: list[tuple[str, float]] = []
        for key, counts in self._terms.items():
            if keys is not None and key not in keys:
                continue
            score = 0.0
            for term in tokens(query):
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                appearances = self._document_frequency[term]
                idf = math.log(1 + (total - appearances + 0.5) / (appearances + 0.5))
                norm = (
                    frequency
                    * (K1 + 1)
                    / (frequency + K1 * (1 - B + B * self._lengths[key] / self._average))
                )
                score += idf * norm
            if score > 0:
                scored.append((key, round(score, 4)))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit] if limit else scored


def _searchable(manifest: SemanticManifest, metric: Metric) -> str:
    """What a metric can be found by: its own words, plus the cuts it authorises."""
    _, owners = metric_sources(manifest.semantic_models, manifest.metrics, metric)
    cuts: list[str] = []
    if owners:
        catalog = dimension_catalog(manifest.semantic_models, manifest.joins, owners[0])
        cuts = [name.replace("__", " ") for name in catalog]
    parts = [metric.name, metric.description, metric.domain or "", *metric.synonyms, *cuts]
    return " ".join(part for part in parts if part)


NAME_MATCH_BONUS = 2.0  # naming a metric outright beats merely sharing words with it


def _names_in(query: str, metric: Metric) -> bool:
    """Does the query contain the metric's own name, or one of its synonyms, in full?"""
    asked = tokens(query)
    for phrase in (metric.name, *metric.synonyms):
        wanted = tokens(phrase)
        if wanted and any(asked[i : i + len(wanted)] == wanted for i in range(len(asked))):
            return True
    return False


class MetricIndex:
    """The catalog, searchable. Built once per manifest; nothing here touches a warehouse."""

    def __init__(self, manifest: SemanticManifest) -> None:
        self.manifest = manifest
        self._index = Bm25({name: _searchable(manifest, m) for name, m in manifest.metrics.items()})

    def search(
        self,
        query: str,
        *,
        domain: str | None = None,
        certified_only: bool = True,
        limit: int = 5,
    ) -> list[tuple[Metric, float]]:
        eligible = {
            name
            for name, metric in self.manifest.metrics.items()
            if (domain is None or metric.domain == domain)
            and (not certified_only or metric.tier == "certified")
        }
        scored = [
            (
                name,
                score + (NAME_MATCH_BONUS if _names_in(query, self.manifest.metrics[name]) else 0),
            )
            for name, score in self._index.rank(query, keys=eligible)
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return [(self.manifest.metrics[name], round(score, 4)) for name, score in scored[:limit]]

    def domains(self) -> list[str]:
        return sorted({m.domain for m in self.manifest.metrics.values() if m.domain})


def rank_names(
    query: str, names: list[str], *, descriptions: dict[str, str] | None = None, limit: int = 25
) -> list[str]:
    """Order candidate names by how well they answer the query, best first.

    Two failures need two tools, so both run. A misspelling (`revenu`) shares no whole token with
    anything, and only fuzzy matching reaches `revenue`. A wrong concept (`colour`) is spelled
    perfectly and only word ranking reaches `product__category` — through its description. What
    neither explains falls through to what actually exists.
    """
    documents = {name: f"{name} {(descriptions or {}).get(name, '')}" for name in names}
    ordered = difflib.get_close_matches(query, names, n=3, cutoff=0.6)
    ordered += [name for name, _ in Bm25(documents).rank(query) if name not in ordered]
    ordered += [name for name in names if name not in ordered]
    return ordered[:limit]
