"""Minimum spanning tree construction.

Two algorithms planned:
    - goeBURST (Francisco et al. 2009) with the usual tie-breaking rules.
    - Classic Prim/Kruskal on the distance graph.

The MST is the centerpiece of the GUI; serialization needs to round-trip cleanly to
Cytoscape.js JSON so the frontend can render without recomputing.

Stub — M5.
"""

from __future__ import annotations

import numpy as np
import networkx as nx


def build_mst(distance: np.ndarray, labels: list[str], algorithm: str = "goeburst") -> nx.Graph:
    raise NotImplementedError("M5 — MST construction not yet implemented.")
