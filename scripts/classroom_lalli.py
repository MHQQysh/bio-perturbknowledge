"""Build, verify, and calculate the classroom comparisons as separate operations."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
from scipy import sparse

from read_record import load_matrix, read_record


GEO_SAMPLE = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM4219576"
PAPER = "https://doi.org/10.1101/gr.262295.120"
PREFIX = "GSM4219576_Run2"
TARGETS = ("ADNP", "ARID1B", "CHD8")


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prepare(source, matrix_file, inputs=None):
    inputs = inputs or source
    matrix = {
        "path": str(matrix_file),
        "format": "mtx",
        "orientation": "genes_by_cells",
        "barcodes_path": str(inputs / f"{PREFIX}_barcodes.tsv.gz"),
        "genes_path": str(inputs / f"{PREFIX}_genes.tsv.gz"),
    }
    labels_file = inputs / f"{PREFIX}_Cell_Guide_Lookup.csv.gz"
    matrix_data = load_matrix(matrix)
    if not matrix_data.obs_names.is_unique or not matrix_data.var_names.is_unique:
        raise ValueError("Count matrix IDs are not unique")
    raw = matrix_data.X.data if sparse.issparse(matrix_data.X) else matrix_data.X
    if not np.isfinite(raw).all() or (raw < 0).any() or not np.equal(raw, np.trunc(raw)).all():
        raise ValueError("GEO counts fail numeric preflight")
    labels = pd.read_csv(labels_file, usecols=["CellBarcode", "sgRNA"]).dropna().drop_duplicates()
    labels["key"] = labels["CellBarcode"].str.replace(r"-\d+$", "", regex=True)
    keys = matrix_data.obs_names.str.replace(r"-\d+$", "", regex=True)
    if not keys.is_unique:
        raise ValueError("Stripping GEM suffix would produce an ambiguous join")
    # Keep one distinct author label per barcode, with no cross-run joins.
    singles = labels.loc[~labels["key"].duplicated(keep=False)].set_index("key")["sgRNA"]
    annotated = singles.reindex(keys).dropna()
    joined = matrix_data[keys.get_indexer(annotated.index)].copy()
    joined.obs["target_gene"] = annotated.str.split("_", n=1).str[0].str.upper().to_numpy()
    controls = annotated.str.fullmatch(r"Nontargeting_\d+", flags=re.IGNORECASE).to_numpy()
    joined.obs["is_control"] = controls
    joined.obs["sample"] = "GSM4219576"
    joined.obs.loc[controls, "target_gene"] = "NTC"
    linked_file = source / "author-labels-linked.h5ad"
    joined.write_h5ad(linked_file, compression="gzip")
    files = [matrix_file, Path(matrix["barcodes_path"]), Path(matrix["genes_path"]), labels_file]
    provenance = {
        "evidence": [
            {"url": PAPER, "locator": "Figure 2B/C and Results, pooled LUHMES single-cell experiment"},
            {"url": GEO_SAMPLE, "locator": "Characteristics, Data processing, and four Supplementary files"},
            {"url": "https://genome.cshlp.org/content/suppl/2020/09/04/gr.262295.120.DC1/Supplemental_Material.pdf",
             "locator": "Supplemental Figure S9, final cell-label abundance"},
        ],
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "join": "One distinct author lookup label per barcode, matched only within Run 2. Strip the matrix GEM suffix only after proving resulting keys are unique.",
        "scale": "GSM4219576 Data processing identifies Cell Ranger filtered gene counts; finite nonnegative integer checks passed.",
    }
    records = []
    for target in TARGETS:
        target_cells = joined.obs_names[joined.obs["target_gene"] == target].tolist()
        control_cells = joined.obs_names[controls].tolist()
        record = {
            "record_id": f"lalli2020_Run2_{target}",
            "study_id": "lalli2020",
            "status": "candidate",
            "context": {"organism": "Homo sapiens", "cell_line": "LUHMES", "sample": "GSM4219576",
                        "time": "GEO sample day 8; article Results describes 7 days at study level"},
            "perturbation": {"modality": "CRISPRi", "target_gene": target, "identity_resolution": "target-level",
                             "assignment_source": "Author Cell_Guide_Lookup CSV, label prefix"},
            "contrast": {"perturbed_cells": target_cells, "control_cells": control_cells,
                         "n_perturbed": len(target_cells), "n_control": len(control_cells),
                         "control_rule": "Same GSM4219576; author Nontargeting labels only, one distinct label per cell"},
            "matrix": {"path": str(linked_file), "format": "h5ad", "layer": "X", "scale": "Cell Ranger raw gene counts",
                       "orientation": "cells_by_genes"},
            "provenance": provenance,
            "limitations": ["One GEO run. Results are descriptive; replication and paper-specific QC were not assessed."],
        }
        records.append(record)
    write_json(source / "lalli-handoff.json", {
        "mode": "new study from primary paper and public author data",
        "sample": "GSM4219576", "candidates": records,
        "source_matrix_shape": list(matrix_data.shape), "single_label_overlap": int(joined.n_obs),
    })
    return {"sample": "GSM4219576", "source_matrix_shape": list(matrix_data.shape),
            "matched_cells": int(joined.n_obs), "candidates": len(records)}


def verify(source):
    handoff = json.loads((source / "lalli-handoff.json").read_text(encoding="utf-8"))
    records = handoff["candidates"]
    summary = []
    for record in records:
        data = read_record(record, require_verified=False)
        checks = data.uns["reader_checks"]
        if not (checks["target_annotation_checked"] and checks["control_annotation_checked"]
                and checks["context_fields_checked"].get("sample")):
            raise ValueError(f"Required cell annotation checks did not run for {record['record_id']}")
        totals = np.asarray(data.X.sum(axis=1)).ravel()
        if np.any(totals <= 0):
            raise ValueError(f"Zero-depth comparison cell for {record['record_id']}")
        summary.append({"record_id": record["record_id"], "shape": list(data.shape),
                        "n_perturbed": record["contrast"]["n_perturbed"], "n_control": record["contrast"]["n_control"],
                        "checks": checks, "decision": "admitted_for_descriptive_analysis"})
        record["status"] = "verified"
    write_json(source / "lalli-receipt.json", {"source_matrix_shape": handoff["source_matrix_shape"],
               "single_label_overlap": handoff["single_label_overlap"], "comparisons": summary,
               "paper_day_disagreement": "Run 2 GEO sample says day 8, article describes 7 days at study level; use sample metadata and record both."})
    write_json(source / "records.json", {"records": records})
    return {"records": summary, "admitted": len(summary)}


def compute(source):
    records = json.loads((source / "records.json").read_text(encoding="utf-8"))["records"]
    for record in records:
        data = read_record(record)
        totals = np.asarray(data.X.sum(axis=1)).ravel()
        if np.any(totals <= 0):
            raise ValueError(f"Zero-depth comparison cell for {record['record_id']}")
        n_perturbed = record["contrast"]["n_perturbed"]
        normalized = data.X.multiply((10000 / totals)[:, None]).tocsr()
        normalized.data = np.log1p(normalized.data)
        perturbed = np.asarray(normalized[:n_perturbed].mean(axis=0)).ravel()
        control = np.asarray(normalized[n_perturbed:].mean(axis=0)).ravel()
        delta = perturbed - control
        pd.DataFrame({"gene_id": data.var_names, "perturbed_mean": perturbed,
                      "control_mean": control, "delta_mean_log1p_cp10k": delta}).to_csv(
                          source / f"{record['record_id']}_response.csv", index=False)
    return {"responses": len(records), "method": "mean(log1p(CP10K)) perturbed minus control"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "verify", "compute"])
    parser.add_argument("source", type=Path)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--inputs", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if args.phase == "prepare":
        if args.matrix is None:
            parser.error("--matrix is required for prepare")
        result = prepare(source, args.matrix.resolve(), args.inputs.resolve() if args.inputs else None)
    elif args.phase == "verify":
        result = verify(source)
    else:
        result = compute(source)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
