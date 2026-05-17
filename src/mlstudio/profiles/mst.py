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
    cluster_threshold: int = 0,
    members_by_rep: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Serialize an MST to Cytoscape.js JSON.

    Adds a `cluster_id` field to every node based on connected components when
    edges with weight > cluster_threshold are removed. With a sensible default
    threshold (e.g. 5 for S. aureus cgMLST) this gives a meaningful colorable
    field even when no STs were assigned.
    """
    elements: list[dict[str, Any]] = []
    metadata = metadata or {}
    st_by_sample = st_by_sample or {}

    # Compute cluster IDs at the chosen threshold
    g = nx.Graph()
    g.add_nodes_from(mst.nodes)
    for u, v, attrs in mst.edges(data=True):
        if int(attrs.get("weight", 0)) <= cluster_threshold:
            g.add_edge(u, v)
    cluster_id: dict[str, int] = {}
    for i, comp in enumerate(sorted(nx.connected_components(g),
                                     key=lambda c: (-len(c), min(c)))):
        for n in comp:
            cluster_id[n] = i + 1

    members_by_rep = members_by_rep or {n: [n] for n in mst.nodes}
    for node in mst.nodes:
        members = members_by_rep.get(node, [node])
        data: dict[str, Any] = {
            "id": node,
            "label": node if len(members) == 1 else f"{node} +{len(members)-1}",
            "members": members,
            "size": len(members),
        }
        if node in st_by_sample and st_by_sample[node]:
            data["st"] = st_by_sample[node]
        data["cluster_id"] = f"C{cluster_id.get(node, 0)}"
        if node in metadata:
            for k, v in metadata[node].items():
                data[k] = v
        # Composition table per metadata field (used for pie-chart rendering)
        if len(members) > 1 and metadata:
            comp: dict[str, dict[str, int]] = {}
            for m in members:
                for k, v in (metadata.get(m) or {}).items():
                    comp.setdefault(k, {})
                    comp[k][str(v)] = comp[k].get(str(v), 0) + 1
            data["composition"] = comp
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
