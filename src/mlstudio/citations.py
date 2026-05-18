"""Literature provenance for MLSTudio's clinical interpretation rules.

Every per-organism cluster threshold and every clinical phenotype flag has
a published source. This module is the single canonical list; the API
serves it at `/api/citations` and the GUI surfaces a citation tooltip on
the relevant column header / setting so a clinical user can trace any
software decision back to a peer-reviewed reference.

Schema for each citation:
    key          : machine identifier (snake_case)
    title        : short paper title
    authors      : first-author surname + et al. + year
    journal      : journal short name
    pmid         : NCBI PubMed ID (or None)
    doi          : DOI (or None)
    summary      : one-line plain-English what it claims
    applies_to   : list of organisms / flag names this citation governs
"""

from __future__ import annotations

CITATIONS: dict[str, dict] = {
    # ---- Per-organism cgMLST outbreak thresholds --------------------------
    "ruppitsch_2015_lmono": {
        "title": "Defining and Evaluating a Core Genome MLST Scheme for L. monocytogenes",
        "authors": "Ruppitsch W et al. 2015",
        "journal": "J Clin Microbiol",
        "pmid": "26354816",
        "doi": "10.1128/JCM.01193-15",
        "summary": "Established the 1748-locus Lmono cgMLST scheme; ≤7 alleles for outbreak relatedness.",
        "applies_to": ["Lmonocytogenes", "outbreak-threshold"],
    },
    "leopold_2014_saureus": {
        "title": "Bacterial whole genome sequencing revisited: portable, scalable and standardized analysis for typing and detection of virulence and antibiotic resistance genes",
        "authors": "Leopold SR et al. 2014",
        "journal": "J Clin Microbiol",
        "pmid": "24759713",
        "doi": "10.1128/JCM.03617-13",
        "summary": "First Saureus cgMLST; SeqSphere default outbreak cut-off ≤24 alleles.",
        "applies_to": ["Saureus", "outbreak-threshold"],
    },
    "higgins_2019_efaecium": {
        "title": "Detection of nosocomial outbreaks by cgMLST in Enterococcus faecium",
        "authors": "Higgins PG et al. 2019",
        "journal": "Front Microbiol",
        "pmid": "30933957",
        "doi": "10.3389/fmicb.2019.00400",
        "summary": "≤3 allele cgMLST cut-off best distinguishes outbreak isolates in Efaecium.",
        "applies_to": ["Efaecium", "outbreak-threshold"],
    },
    "higgins_2017_abaumannii": {
        "title": "Development of a core genome multilocus sequence typing scheme for high-resolution typing of Acinetobacter baumannii",
        "authors": "Higgins PG et al. 2017",
        "journal": "J Clin Microbiol",
        "pmid": "28659323",
        "doi": "10.1128/JCM.00785-17",
        "summary": "Acinetobacter baumannii cgMLST; outbreak cutoff ≤6 alleles.",
        "applies_to": ["Abaumannii", "outbreak-threshold"],
    },
    "miro_2020_kpneumoniae": {
        "title": "Molecular Epidemiology of CPE Klebsiella pneumoniae using cgMLST",
        "authors": "Miró E et al. 2020",
        "journal": "Antimicrob Resist Infect Control",
        "pmid": "32393371",
        "doi": "10.1186/s13756-020-00734-5",
        "summary": "KP-complex cgMLST outbreak threshold ≤15 alleles (Ridom default).",
        "applies_to": ["Kpneumoniae_complex", "outbreak-threshold"],
    },

    # ---- AMRFinderPlus methodology + Plus database ------------------------
    "feldgarden_2019_amrfinder": {
        "title": "Validating the AMRFinder Tool and Resistance Gene Database by Using Antimicrobial Resistance Genotype-Phenotype Correlations",
        "authors": "Feldgarden M et al. 2019",
        "journal": "AAC",
        "pmid": "31427293",
        "doi": "10.1128/AAC.00483-19",
        "summary": "98.4 % genotype-phenotype concordance for AMRFinder; basis for clinical use.",
        "applies_to": ["amrfinder"],
    },
    "feldgarden_2021_plus": {
        "title": "AMRFinderPlus and the Reference Gene Catalog facilitate examination of the genomic links among AMR, stress response, and virulence",
        "authors": "Feldgarden M et al. 2021",
        "journal": "Sci Rep",
        "pmid": "34135395",
        "doi": "10.1038/s41598-021-91456-0",
        "summary": "Adds the --plus panel (virulence + stress + biocide); curated NCBI Reference Gene Catalog.",
        "applies_to": ["amrfinder", "amr-plus"],
    },

    # ---- HierCC / Enterobase nomenclature --------------------------------
    "zhou_2021_hiercc": {
        "title": "HierCC: a multi-level clustering scheme for population assignments based on core genome MLST",
        "authors": "Zhou Z et al. 2021",
        "journal": "Bioinformatics",
        "pmid": "33823553",
        "doi": "10.1093/bioinformatics/btab234",
        "summary": "Defines HC0/HC2/HC5/HC10/HC50 etc. — the hierarchical cgMLST nomenclature MLSTudio mirrors locally.",
        "applies_to": ["HierCC", "cgst-nomenclature"],
    },

    # ---- Clinical phenotype rules ----------------------------------------
    "magiorakos_2012_mdrxdr": {
        "title": "Multidrug-resistant, extensively drug-resistant and pandrug-resistant bacteria: an international expert proposal for interim standard definitions for acquired resistance",
        "authors": "Magiorakos AP et al. 2012",
        "journal": "Clin Microbiol Infect",
        "pmid": "21793988",
        "doi": "10.1111/j.1469-0691.2011.03570.x",
        "summary": "Consensus MDR/XDR definitions (≥3 drug classes for MDR, ≥5 / all-but-2 for XDR).",
        "applies_to": ["MDR", "XDR", "possible MDR", "possible XDR"],
    },
    "ito_2014_mecA": {
        "title": "Insights on antimicrobial resistance, public health, and possible treatment options of Staphylococcus aureus",
        "authors": "Ito T et al. 2014 / IWG-SCC consensus",
        "journal": "J Antimicrob Chemother",
        "pmid": "27733512",
        "doi": "10.1093/jac/dkw446",
        "summary": "mecA / mecC presence defines methicillin-resistant Staphylococcus aureus (MRSA).",
        "applies_to": ["MRSA"],
    },
    "courvalin_2006_van": {
        "title": "Vancomycin resistance in Gram-positive cocci",
        "authors": "Courvalin P. 2006",
        "journal": "Clin Infect Dis",
        "pmid": "16323120",
        "doi": "10.1086/491711",
        "summary": "vanA/vanB/vanC genotype → glycopeptide resistance phenotype in enterococci (VRE).",
        "applies_to": ["VRE", "VRE-vanA", "VRE-vanB", "VRSA"],
    },
    "bush_2010_betalactamase": {
        "title": "Updated functional classification of beta-lactamases",
        "authors": "Bush K & Jacoby GA. 2010",
        "journal": "AAC",
        "pmid": "20065054",
        "doi": "10.1128/AAC.01009-09",
        "summary": "ESBL = extended-spectrum β-lactamase (CTX-M, plus ESBL variants of SHV / TEM).",
        "applies_to": ["ESBL+"],
    },
    "nordmann_2012_cre": {
        "title": "Carbapenem resistance in Enterobacteriaceae: here is the storm!",
        "authors": "Nordmann P et al. 2012",
        "journal": "Trends Mol Med",
        "pmid": "22516901",
        "doi": "10.1016/j.molmed.2012.03.003",
        "summary": "CPE = carbapenemase-producing Enterobacterales (KPC, NDM, VIM, IMP, OXA-48 family).",
        "applies_to": ["CPE", "CRE"],
    },
}


def get_citations_for(tag: str) -> list[dict]:
    """All citations whose `applies_to` includes the given tag."""
    return [c for c in CITATIONS.values() if tag in c.get("applies_to", [])]


def citation_link(c: dict) -> str:
    """Best public URL for the citation."""
    if c.get("doi"):
        return f"https://doi.org/{c['doi']}"
    if c.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{c['pmid']}/"
    return ""


def citation_string(c: dict) -> str:
    """One-liner citation suitable for a tooltip."""
    return f"{c['authors']}, {c['journal']}. {c['title']}"
