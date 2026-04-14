"""Diagnostic: analyze top-ranked datasets for gout single-cell relevance"""
import urllib.request
import json
import ssl
import sys

sys.stdout.reconfigure(encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

GSE_IDS = [
    "GSE217561", "GSE211783", "GSE258959", "GSE272217", "GSE256431",
    "GSE158055", "GSE160308", "GSE160306", "SRP387842", "GSE188280",
    "GSE189228", "GSE108395887", "GSE108098501", "GSE108098500",
    "GSE18002", "GSE169052", "GSE169051", "GSE307602",
]

# Disease keywords for gout/hyperuricemia
DISEASE_TERMS = [
    "gout", "hyperuricemia", "uric", "urate", "msu", "tophus",
    "podagra", "gouty", "gouty arthritis", "monosodium urate",
    "uric acid", "hurat", "urica",
]

# Single-cell methodology keywords
SC_TERMS = [
    "single-cell", "scrna", "scrnaseq", "scseq", "singlecell",
    "single cell", "single-cell rna", "single cell rna",
    "snrna", "snrnaseq", "single-cell transcriptome",
    "10x genomics", "cellranger", "multiome", "cite-seq",
    "single-nucleus",
]


def fetch_gse_info(gse_id: str) -> dict:
    """Fetch GSE title + summary from GEO API"""
    try:
        url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=gds&term={gse_id}&retmax=1&retmode=json"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read())
            ids = data.get("esearchresult", {}).get("idlist", [])
            if not ids:
                return {"title": "NOT FOUND", "summary": ""}
            uid = ids[0]
        url2 = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=gds&id={uid}&retmode=json"
        req2 = urllib.request.Request(url2)
        with urllib.request.urlopen(req2, timeout=15, context=ctx) as resp2:
            data2 = json.loads(resp2.read())
            r = data2.get("result", {}).get(uid, {})
            return {
                "title": r.get("title", "N/A"),
                "summary": r.get("summary", ""),
            }
    except Exception as e:
        return {"title": f"ERROR: {e}", "summary": ""}


def manual_analysis(gse_id: str, title: str, summary: str) -> dict:
    """Manually analyze relevance - disease + sc methodology"""
    text = (title + " " + summary).lower()

    disease_matches = [t for t in DISEASE_TERMS if t in text]
    sc_matches = [t for t in SC_TERMS if t in text]

    # Determine relevance
    if disease_matches and sc_matches:
        verdict = "[RELEVANT] Direct gout + single-cell"
    elif disease_matches and not sc_matches:
        verdict = "[PARTIAL] Gout but NOT single-cell"
    elif not disease_matches and sc_matches:
        verdict = "[NOISE]   Single-cell but NOT gout-related"
    else:
        verdict = "[GARBAGE] Neither"

    return {
        "verdict": verdict,
        "disease": disease_matches,
        "sc": sc_matches,
    }


print(f"{'#':<3} {'Accession':<15} {'Verdict':<35} Disease Matches            SC Matches")
print("-" * 120)

all_results = {}
for i, gid in enumerate(GSE_IDS):
    info = fetch_gse_info(gid)
    result = manual_analysis(gid, info["title"], info["summary"])
    all_results[gid] = {"title": info["title"], **result}

    dm = ", ".join(result["disease"]) if result["disease"] else "(none)"
    sm = ", ".join(result["sc"]) if result["sc"] else "(none)"
    print(f"{i+1:<3} {gid:<15} {result['verdict']:<35} {dm:<28} {sm}")

print("\n" + "=" * 120)
print("DETAILED TITLES:")
print("=" * 120)
for i, gid in enumerate(GSE_IDS):
    r = all_results[gid]
    print(f"\n{i+1:>2}. {gid}")
    print(f"    [{r['verdict']}]")
    print(f"    Title: {r['title']}")
