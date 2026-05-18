"""Persistent local typing nomenclature.

Maps stable cgMLST profile hashes to short, monotonically-assigned integers
that *stay consistent across runs* on the same machine — so the cgST you get
for an isolate today is the same cgST you get tomorrow, and re-running an
outbreak panel after adding new samples keeps the old isolates in their old
clusters instead of renumbering everything.

This is the same idea as Ridom SeqSphere's *Complex Type* numbers and
Enterobase HierCC's HC IDs, except that both of those services maintain a
**centrally curated** numbering — different labs that submit the same profile
get the same number. We can't replicate that without their accounts, so the
numbers we assign here are *machine-local*. The user's first analysis seeds
the numbering, every subsequent analysis reuses + extends it.

Files live at ``~/.local/share/mlstudio/nomenclature/<scheme_key>.json``::

    {
      "version": 1,
      "scheme_key": "efaecium_cgmlst_orgio",
      "cgst": {                      # cgST profile hash → sequential int
        "7bd19351": 1,
        "4a7eba8c": 2,
        …
      },
      "clusters": {                  # threshold (alleles) → { cgst_id → cluster_id }
        "5":  {"1": 1, "2": 1, "3": 1, "4": 2},
        "10": {"1": 1, "2": 1, "3": 1, "4": 1, "5": 2},
        …
      }
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from mlstudio.schemes.bigsdb import cache_root

_LOCK = Lock()


def nomenclature_root() -> Path:
    return cache_root().parent / "nomenclature"


class NomenclatureStore:
    """Thread-safe local typing nomenclature for one scheme.

    Read/write through `assign_cgst()` and `assign_cluster()`; both persist
    the underlying JSON file atomically on every change so a crashed run
    never leaves the store in a partial state.
    """

    def __init__(self, scheme_key: str) -> None:
        self.scheme_key = scheme_key
        self.path = nomenclature_root() / f"{scheme_key}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except Exception:
                self.data = {}
        else:
            self.data = {}
        self.data.setdefault("version", 1)
        self.data.setdefault("scheme_key", self.scheme_key)
        self.data.setdefault("cgst", {})
        self.data.setdefault("clusters", {})

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # ---- cgST: stable per-profile integer ---------------------------------

    def assign_cgst(self, profile_hash: str) -> int:
        """Return the sequential cgST integer for this profile hash,
        assigning a fresh one the first time it's seen."""
        with _LOCK:
            existing = self.data["cgst"].get(profile_hash)
            if existing is not None:
                return int(existing)
            next_id = max((int(v) for v in self.data["cgst"].values()), default=0) + 1
            self.data["cgst"][profile_hash] = next_id
            self._save()
            return next_id

    # ---- Cluster IDs at a given allele-distance threshold -----------------

    def assign_cluster(self, threshold: int, cgst_ids: list[int],
                       union_with: list[int] | None = None) -> int:
        """Look up (or assign) the cluster ID at `threshold` for a *set* of
        cgST IDs that are within `threshold` alleles of each other.

        Behaviour:
        - If any member is already in a cluster, reuse that cluster ID and
          enrol the rest. If members are split across multiple existing
          clusters, merge them (lowest ID wins).
        - Otherwise assign a fresh cluster ID.

        `union_with` is a list of cgST IDs that are *also* within
        `threshold` of the new ones — used when the caller has computed
        the full transitive closure of the threshold-graph and wants the
        cluster IDs unified.
        """
        with _LOCK:
            level = self.data["clusters"].setdefault(str(threshold), {})
            members = list(cgst_ids) + list(union_with or [])
            existing_ids = sorted({int(level[str(m)]) for m in members
                                   if str(m) in level})

            if existing_ids:
                # Merge — winner is the smallest existing cluster ID.
                cluster_id = existing_ids[0]
                # Re-tag everything currently in any of the existing IDs.
                for cgst_str, cid in list(level.items()):
                    if int(cid) in existing_ids:
                        level[cgst_str] = cluster_id
            else:
                taken = {int(v) for v in level.values()}
                cluster_id = (max(taken) + 1) if taken else 1
            for m in members:
                level[str(m)] = cluster_id
            self._save()
            return cluster_id

    def cluster_for(self, threshold: int, cgst_id: int) -> int | None:
        level = self.data["clusters"].get(str(threshold), {})
        v = level.get(str(cgst_id))
        return int(v) if v is not None else None

    def snapshot(self) -> dict:
        """Defensive copy of the underlying state — for serialisation."""
        with _LOCK:
            return json.loads(json.dumps(self.data))
