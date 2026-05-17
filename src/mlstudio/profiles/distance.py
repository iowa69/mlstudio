"""Allele-profile distance computations.

Hamming distance over allele profiles, ignoring missing calls per the user-configured
policy (pairwise-complete by default). Vectorized over numpy arrays of int allele IDs.

Stub — M5.
"""

from __future__ import annotations

import numpy as np


def hamming_matrix(profiles: np.ndarray, ignore_missing: bool = True) -> np.ndarray:
    raise NotImplementedError("M5 — distance matrix not yet implemented.")
