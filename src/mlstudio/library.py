"""Persistent sample library.

Every analyzed isolate is stamped into a local SQLite database so the user
can browse, re-load, and compare samples across projects — the closest
equivalent in SeqSphere+ is the central "Samples" tab. This is a pure
sidecar; analyses never depend on it. Lives at
``~/.local/share/mlstudio/library.sqlite``.

Schema (kept deliberately small):

    samples (
        sample_key   TEXT PRIMARY KEY,   -- stable hash of folder + name + scheme
        sample_name  TEXT,
        scheme_key   TEXT,
        organism     TEXT,
        st           TEXT,
        cgst_id      INTEGER,
        cgst_hash    TEXT,
        clonal_complex TEXT,
        cluster_hc10 TEXT,
        amr_flags    TEXT,   -- JSON-encoded list
        qc_verdict   TEXT,
        analyzed_at  TIMESTAMP,
        folder       TEXT,
        assembly_path TEXT,
        snapshot_json TEXT   -- the full per-sample result_dict
    )

The denormalised columns make the table-browse / filter view fast without
touching `snapshot_json`; loading a sample into an analysis pulls the JSON
blob and rehydrates the result_dict shape the GUI already understands.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from mlstudio.schemes.bigsdb import cache_root

_LOCK = threading.Lock()


def library_path() -> Path:
    return cache_root().parent / "library.sqlite"


def _sample_key(folder: str, sample_name: str, scheme_key: str) -> str:
    raw = f"{folder}|{sample_name}|{scheme_key}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@contextmanager
def _conn():
    path = library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        con.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                sample_key      TEXT PRIMARY KEY,
                sample_name     TEXT,
                scheme_key      TEXT,
                organism        TEXT,
                st              TEXT,
                cgst_id         INTEGER,
                cgst_hash       TEXT,
                clonal_complex  TEXT,
                cluster_hc10    TEXT,
                amr_flags       TEXT,
                qc_verdict      TEXT,
                analyzed_at     TIMESTAMP,
                folder          TEXT,
                assembly_path   TEXT,
                snapshot_json   TEXT
            )
        """)
        # Indexes for the filter UI.
        con.execute("CREATE INDEX IF NOT EXISTS idx_scheme  ON samples(scheme_key)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_organism ON samples(organism)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_st       ON samples(st)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_analyzed ON samples(analyzed_at DESC)")
        try:
            yield con
            con.commit()
        finally:
            con.close()


def save_sample(snapshot: dict[str, Any], folder: str, scheme_key: str,
                organism: str | None = None) -> str:
    """Upsert one analyzed sample into the library."""
    sample_name = snapshot.get("sample") or ""
    key = _sample_key(folder, sample_name, scheme_key)
    row = (
        key,
        sample_name,
        scheme_key,
        organism or snapshot.get("scheme"),
        snapshot.get("st"),
        snapshot.get("cgst_id"),
        snapshot.get("cgst"),
        snapshot.get("clonal_complex"),
        (snapshot.get("hier") or {}).get("HC10"),
        json.dumps(snapshot.get("amr_flags") or []),
        (snapshot.get("qc") or {}).get("verdict"),
        datetime.utcnow().isoformat(timespec="seconds"),
        folder,
        (snapshot.get("input") or {}).get("assembly"),
        json.dumps(snapshot),
    )
    with _conn() as con:
        con.execute("""
            INSERT INTO samples (sample_key, sample_name, scheme_key, organism,
                st, cgst_id, cgst_hash, clonal_complex, cluster_hc10,
                amr_flags, qc_verdict, analyzed_at, folder, assembly_path, snapshot_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(sample_key) DO UPDATE SET
                sample_name=excluded.sample_name,
                scheme_key=excluded.scheme_key,
                organism=excluded.organism,
                st=excluded.st,
                cgst_id=excluded.cgst_id,
                cgst_hash=excluded.cgst_hash,
                clonal_complex=excluded.clonal_complex,
                cluster_hc10=excluded.cluster_hc10,
                amr_flags=excluded.amr_flags,
                qc_verdict=excluded.qc_verdict,
                analyzed_at=excluded.analyzed_at,
                folder=excluded.folder,
                assembly_path=excluded.assembly_path,
                snapshot_json=excluded.snapshot_json
        """, row)
    return key


def list_samples(*, q: str | None = None, scheme: str | None = None,
                 organism: str | None = None, flag: str | None = None,
                 limit: int = 500) -> list[dict[str, Any]]:
    """Lightweight projection for the Library tab."""
    where = []
    params: list[Any] = []
    if scheme:
        where.append("scheme_key = ?")
        params.append(scheme)
    if organism:
        where.append("organism = ?")
        params.append(organism)
    if flag:
        where.append("amr_flags LIKE ?")
        params.append(f"%{flag}%")
    if q:
        like = f"%{q.lower()}%"
        where.append(
            "(LOWER(sample_name) LIKE ? OR LOWER(IFNULL(st,'')) LIKE ? "
            " OR LOWER(IFNULL(clonal_complex,'')) LIKE ? "
            " OR LOWER(IFNULL(amr_flags,'')) LIKE ?)"
        )
        params += [like, like, like, like]
    sql = (
        "SELECT sample_key, sample_name, scheme_key, organism, st, cgst_id,"
        " clonal_complex, cluster_hc10, amr_flags, qc_verdict, analyzed_at,"
        " folder, assembly_path"
        " FROM samples"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY analyzed_at DESC LIMIT ?"
    params.append(limit)
    out: list[dict[str, Any]] = []
    with _conn() as con:
        for row in con.execute(sql, params):
            r = dict(row)
            try:
                r["amr_flags"] = json.loads(r["amr_flags"] or "[]")
            except json.JSONDecodeError:
                r["amr_flags"] = []
            out.append(r)
    return out


def get_sample(sample_key: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            "SELECT snapshot_json, folder, scheme_key FROM samples WHERE sample_key = ?",
            (sample_key,),
        ).fetchone()
    if not row:
        return None
    snap = json.loads(row["snapshot_json"])
    snap["_folder"] = row["folder"]
    snap["_scheme"] = row["scheme_key"]
    return snap


def delete_sample(sample_key: str) -> bool:
    with _conn() as con:
        cur = con.execute("DELETE FROM samples WHERE sample_key = ?", (sample_key,))
    return cur.rowcount > 0


def stats() -> dict[str, Any]:
    """Summary numbers for the Library tab header."""
    with _conn() as con:
        total = con.execute("SELECT COUNT(*) AS n FROM samples").fetchone()["n"]
        per_organism = [dict(r) for r in con.execute(
            "SELECT organism, COUNT(*) AS n FROM samples"
            " GROUP BY organism ORDER BY n DESC LIMIT 20"
        )]
        per_scheme = [dict(r) for r in con.execute(
            "SELECT scheme_key, COUNT(*) AS n FROM samples"
            " GROUP BY scheme_key ORDER BY n DESC LIMIT 20"
        )]
    return {"total": total, "per_organism": per_organism, "per_scheme": per_scheme}
