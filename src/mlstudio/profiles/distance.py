"""Allele-profile distance computations.

Hamming distance over allele profiles (string identifiers), ignoring missing
calls per the user policy.

Input: a dict {sample_name: [allele_or_None_per_locus]}.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class DistanceMatrix:
    samples: list[str]
    matrix: np.ndarray   # (n, n) int

    @property
    def n(self) -> int:
        return len(self.samples)

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {
            self.samples[i]: {self.samples[j]: int(self.matrix[i, j]) for j in range(self.n)}
            for i in range(self.n)
        }

    def pairs_under(self, threshold: int) -> list[tuple[str, str, int]]:
        """All distinct pairs with distance ≤ threshold."""
        out = []
        for i in range(self.n):
            for j in range(i + 1, self.n):
                w = int(self.matrix[i, j])
                if w <= threshold:
                    out.append((self.samples[i], self.samples[j], w))
        return out


def hamming_matrix(
    profiles: dict[str, list[str | None]],
    policy: str = "pairwise_complete",
) -> DistanceMatrix:
    """Pairwise Hamming distance over allele profiles.

    policy:
        "pairwise_complete" — ignore loci missing in either profile (default,
            standard for cgMLST in SeqSphere-like tools).
        "count_missing" — treat any missing locus as a difference (more
            conservative; isolates with poor calling get pushed apart).
        "scaled" — pairwise_complete distance scaled up to the full locus
            count: d * n_loci / n_shared (preserves relative magnitude).
    """
    samples = list(profiles.keys())
    n = len(samples)
    n_loci = len(next(iter(profiles.values()))) if samples else 0
    mat = np.zeros((n, n), dtype=np.int32)

    for i in range(n):
        pi = profiles[samples[i]]
        for j in range(i + 1, n):
            pj = profiles[samples[j]]
            d = 0
            shared = 0
            for a, b in zip(pi, pj, strict=True):
                if policy == "missing_as_category":
                    # Treat None as its own allele value (Ridom's "missing as
                    # own category"). Two missing values are equal; missing
                    # vs called counts as a difference.
                    if a != b:
                        d += 1
                    shared += 1
                    continue
                if a is None or b is None:
                    if policy == "count_missing":
                        d += 1
                    # pairwise_complete / scaled: skip
                else:
                    shared += 1
                    if a != b:
                        d += 1
            if policy == "scaled" and shared > 0 and n_loci > 0:
                d = round(d * n_loci / shared)
            if policy == "pairwise_complete" and shared == 0:
                d = n_loci if n_loci > 0 else 1
            mat[i, j] = d
            mat[j, i] = d

    return DistanceMatrix(samples=samples, matrix=mat)
