"""Pure-Python unit tests for distance + MST serialization.

Locks in the n-1 tree-edges contract that regressed when non-tree edges were
added to the Cytoscape output (commit fffd952).
"""

from __future__ import annotations

from mlstudio.profiles.distance import hamming_matrix
from mlstudio.profiles.mst import build_mst, mst_to_cytoscape


def _toy_profiles() -> dict[str, list[str | None]]:
    return {
        "A": ["1", "1", "1", "1"],
        "B": ["1", "1", "1", "2"],   # 1 from A
        "C": ["1", "1", "3", "2"],   # 2 from A, 1 from B
        "D": ["9", "9", "9", "9"],   # far from everyone
    }


def test_hamming_distinct_and_symmetric() -> None:
    dm = hamming_matrix(_toy_profiles())
    assert dm.samples == ["A", "B", "C", "D"]
    d = dm.as_dict()
    assert d["A"]["A"] == 0
    assert d["A"]["B"] == 1
    assert d["B"]["C"] == 1
    assert d["A"]["C"] == 2
    assert d["A"]["D"] == 4
    # symmetry
    for s in dm.samples:
        for t in dm.samples:
            assert d[s][t] == d[t][s]


def test_mst_has_n_minus_one_tree_edges() -> None:
    dm = hamming_matrix(_toy_profiles())
    mst = build_mst(dm)
    cyto = mst_to_cytoscape(mst)

    nodes = [el for el in cyto["elements"] if "source" not in el["data"]]
    edges = [el for el in cyto["elements"] if "source" in el["data"]]
    tree_edges = [e for e in edges if e["data"]["kind"] == "tree"]

    assert {n["data"]["id"] for n in nodes} == {"A", "B", "C", "D"}
    assert len(tree_edges) == 3  # n - 1 for n = 4
    assert all(e["data"]["weight"] > 0 for e in tree_edges)


def test_non_tree_edges_render_separately_and_dedup_with_mst() -> None:
    dm = hamming_matrix(_toy_profiles())
    mst = build_mst(dm)

    # Pretend A-C (distance 2) is below the user threshold but not in the MST,
    # AND pretend A-B (distance 1) is — it should be deduped against the tree.
    cyto = mst_to_cytoscape(
        mst,
        non_tree_pairs=[("A", "C", 2), ("A", "B", 1)],
    )
    edges = [el for el in cyto["elements"] if "source" in el["data"]]
    by_kind: dict[str, list[dict]] = {"tree": [], "nontree": []}
    for e in edges:
        by_kind[e["data"]["kind"]].append(e["data"])

    assert len(by_kind["tree"]) == 3
    # A-B is already an MST edge, so the non-tree input for it must NOT show up;
    # only the genuinely-non-tree A-C pair should.
    assert len(by_kind["nontree"]) == 1
    assert {by_kind["nontree"][0]["source"], by_kind["nontree"][0]["target"]} == {"A", "C"}
    assert by_kind["nontree"][0]["weight"] == 2


def test_cluster_id_groups_close_isolates() -> None:
    dm = hamming_matrix(_toy_profiles())
    mst = build_mst(dm)
    # Threshold 1 should cluster A-B-C (all within 1-hop chains of weight 1)
    # but leave D alone.
    cyto = mst_to_cytoscape(mst, cluster_threshold=1)
    cid = {el["data"]["id"]: el["data"]["cluster_id"]
           for el in cyto["elements"] if "source" not in el["data"]}
    assert cid["A"] == cid["B"] == cid["C"]
    assert cid["D"] != cid["A"]
