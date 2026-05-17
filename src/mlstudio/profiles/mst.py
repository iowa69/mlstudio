"""Minimum spanning tree on an allele-distance matrix.

For v1 we use NetworkX's Kruskal MST on the complete graph of pairwise distances.
goeBURST tie-breaking can be added later (see ROADMAP M5).

Output: a Cytoscape.js-compatible JSON dict ready for the frontend.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from mlstudio.profiles.distance import DistanceMatrix


def build_mst(dm: DistanceMatrix) -> nx.Graph:
    g = nx.Graph()
    for i, s in enumerate(dm.samples):
        g.add_node(s)
    for i in range(dm.n):
        for j in range(i + 1, dm.n):
            g.add_edge(dm.samples[i], dm.samples[j], weight=int(dm.matrix[i, j]))
    return nx.minimum_spanning_tree(g, algorithm="kruskal")


def mst_to_cytoscape(
    mst: nx.Graph,
    metadata: dict[str, dict[str, Any]] | None = None,
    st_by_sample: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Serialize an MST to Cytoscape.js JSON.

    Each node carries any metadata + ST so the frontend can color/size by field.
    Edge labels are the inter-isolate allele distance.
    """
    elements: list[dict[str, Any]] = []
    metadata = metadata or {}
    st_by_sample = st_by_sample or {}

    for node in mst.nodes:
        data: dict[str, Any] = {"id": node, "label": node}
        if node in st_by_sample and st_by_sample[node]:
            data["st"] = st_by_sample[node]
        if node in metadata:
            for k, v in metadata[node].items():
                data[k] = v
        elements.append({"data": data})

    for u, v, attrs in mst.edges(data=True):
        elements.append({
            "data": {
                "id": f"{u}__{v}",
                "source": u,
                "target": v,
                "weight": int(attrs.get("weight", 0)),
                "label": str(attrs.get("weight", 0)),
            }
        })
    return {"elements": elements}
