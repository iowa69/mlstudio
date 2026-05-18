"""Clinical interpretation of AMRFinderPlus results.

Translates the raw gene-level output of `amrfinder --plus` into the
clinical phenotype summary a microbiologist actually reads: a list of
affected drug classes, a few binary clinical flags (MRSA, VRE, ESBL,
CRE, CPE, VRSA/VISA), and a multi-drug-resistance summary.

The rules below follow the consensus used by NCBI Pathogen Detection,
ECDC, and CDC reports. AMRFinderPlus reports *gene presence*, not
phenotypic resistance — see Feldgarden et al. 2019 (AAC), Feldgarden
et al. 2021 (Sci Rep) — so anything we infer here is a probabilistic
phenotype call, surfaced alongside the genotype so the user can audit
the chain of evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mlstudio.amr.amrfinderplus import AmrHit

# ---- Clinical flag definitions ---------------------------------------------
# Each entry: human-readable flag → callable(hit, organism) -> bool. The
# organism string is the AMRFinderPlus `--organism` we passed (Staphylococcus_aureus,
# Enterococcus_faecium, …) so the rule can be species-aware where needed.

_MRSA_RX  = re.compile(r"^mec[ABC]$")
_VAN_RX   = re.compile(r"^van[A-N]$")
_CTX_M_RX = re.compile(r"^blaCTX-M", re.I)
_SHV_RX   = re.compile(r"^blaSHV", re.I)
_TEM_RX   = re.compile(r"^blaTEM", re.I)
_OXA_RX   = re.compile(r"^blaOXA", re.I)
_KPC_RX   = re.compile(r"^blaKPC", re.I)
_NDM_RX   = re.compile(r"^blaNDM", re.I)
_VIM_RX   = re.compile(r"^blaVIM", re.I)
_IMP_RX   = re.compile(r"^blaIMP", re.I)
_GES_RX   = re.compile(r"^blaGES", re.I)
_OXA48_RX = re.compile(r"^blaOXA-(48|181|232|244|436|484)", re.I)


def _is_amr(h: AmrHit) -> bool:
    """Heuristic: include AMR + POINT-mutation hits, exclude virulence/stress
    when computing the resistance summary. Virulence is shown separately."""
    et = (h.element_type or "").upper()
    return et in ("AMR", "POINT") or "AMR" in (h.element_subtype or "").upper()


def _is_carbapenemase(gene: str) -> bool:
    """Class-A KPC + Class-B metallos (NDM, VIM, IMP, GIM, SPM) + the
    carbapenem-hydrolysing class-D OXAs (the OXA-48 family). Plain blaOXA-1
    etc. are NOT carbapenemases — only the OXA-48 family."""
    return any(rx.match(gene) for rx in (_KPC_RX, _NDM_RX, _VIM_RX, _IMP_RX)) \
        or _OXA48_RX.match(gene) is not None


def _is_esbl(hit: AmrHit) -> bool:
    """ESBL = extended-spectrum β-lactamase. CTX-M family is unambiguously
    ESBL. TEM / SHV families contain *both* narrow-spectrum and ESBL variants;
    AMRFinderPlus annotates ESBL ones in the subclass / element_subtype
    field, which we use as the discriminator."""
    g = hit.gene_symbol or ""
    if _CTX_M_RX.match(g):
        return True
    if _SHV_RX.match(g) or _TEM_RX.match(g):
        # Subclass like "CEPHALOSPORIN" / "EXTENDED-SPECTRUM-BETA-LACTAMASE"
        # signals the ESBL variant.
        sub = (hit.subclass or "").upper()
        return "ESBL" in sub or "EXTENDED" in sub
    return False


@dataclass(slots=True)
class ResistanceSummary:
    """Per-sample clinical interpretation of an AMRFinderPlus run."""
    sample: str
    organism: str | None = None
    drug_classes: dict[str, list[str]] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    n_amr_genes: int = 0
    n_virulence: int = 0
    n_stress: int = 0
    summary_line: str = ""


def interpret(hits: list[AmrHit], sample: str,
              organism: str | None = None) -> ResistanceSummary:
    """Bucket AMRFinderPlus hits into clinical-style summaries.

    Returns a ResistanceSummary with:
      - drug_classes  {GLYCOPEPTIDE: [vanA], BETA-LACTAM: [mecA, blaZ], …}
      - flags         ["MRSA", "VRE-vanA", "ESBL+", "CPE-NDM-1"]
      - count buckets (amr / virulence / stress)
      - summary_line  human-readable one-liner
    """
    rs = ResistanceSummary(sample=sample, organism=organism)
    amr_hits: list[AmrHit] = []
    for h in hits:
        et = (h.element_type or "").upper()
        if et == "VIRULENCE":
            rs.n_virulence += 1
        elif et == "STRESS":
            rs.n_stress += 1
        else:
            amr_hits.append(h)
    rs.n_amr_genes = len(amr_hits)

    # Bucket by NCBI drug "class" (CARBAPENEM, GLYCOPEPTIDE, …).
    for h in amr_hits:
        cls = (h.class_ or "OTHER").upper().strip() or "OTHER"
        rs.drug_classes.setdefault(cls, []).append(h.gene_symbol)
    # De-dup gene lists; sort for readability.
    for cls in rs.drug_classes:
        rs.drug_classes[cls] = sorted(set(rs.drug_classes[cls]))

    # ---- Clinical flags ---------------------------------------------------
    organism_l = (organism or "").lower()
    gene_set = {h.gene_symbol for h in amr_hits}

    # MRSA / MRSE: mecA/mecC in a Staphylococcus genome
    if any(_MRSA_RX.match(g) for g in gene_set) and "staphylococcus" in organism_l:
        rs.flags.append("MRSA")

    # VRE: vanA/vanB (etc.) in an Enterococcus genome
    if any(_VAN_RX.match(g) for g in gene_set) and "enterococcus" in organism_l:
        # Distinguish vanA (high-level, transferable) vs vanB (variable) etc.
        for v in sorted(g for g in gene_set if _VAN_RX.match(g)):
            rs.flags.append(f"VRE-{v}")

    # VRSA / VISA: vanA in Saureus (very rare but clinically catastrophic)
    if any(_VAN_RX.match(g) for g in gene_set) and "aureus" in organism_l:
        rs.flags.append("VRSA")

    # ESBL+: extended-spectrum β-lactamase in Enterobacterales
    if any(_is_esbl(h) for h in amr_hits):
        rs.flags.append("ESBL+")

    # CPE: carbapenemase-producing Enterobacterales (or other gram-neg)
    cpe_genes = sorted(g for g in gene_set if _is_carbapenemase(g))
    if cpe_genes:
        # Tag with the carbapenemase family for the most informative label
        # (CPE-KPC-3 / CPE-NDM-1 / CPE-OXA-48 / …).
        rs.flags.append(f"CPE-{cpe_genes[0]}")

    # MDR / XDR very rough heuristic: ≥3 drug classes with non-intrinsic
    # resistance → MDR; ≥5 → XDR. Real MDR/XDR definitions (Magiorakos 2012)
    # are phenotype-based; this is a genotypic proxy.
    n_classes = sum(1 for cls in rs.drug_classes
                    if cls not in ("OTHER", ""))
    if n_classes >= 5:
        rs.flags.append("possible XDR")
    elif n_classes >= 3:
        rs.flags.append("possible MDR")

    # Compact one-line summary for the MST tooltip / table column.
    flag_str = " ".join(rs.flags) if rs.flags else "—"
    if rs.drug_classes:
        cls_str = "; ".join(f"{cls.lower()}: {','.join(genes)}"
                            for cls, genes in sorted(rs.drug_classes.items()))
        rs.summary_line = f"{flag_str} | {cls_str}"
    else:
        rs.summary_line = flag_str
    return rs
