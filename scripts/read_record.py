"""Load the actual perturbed and control cells referenced by a project record.

Run on SSHSCY with the project virtual environment. No normalization is applied.
"""
import argparse
import gzip
import json
from pathlib import Path

import anndata as ad
import h5py
import numpy as np
import pandas as pd
from scipy import io, sparse


def cell_ids(descriptor):
    if isinstance(descriptor, list):
        return [str(value) for value in descriptor]
    path = Path(descriptor["path"] if isinstance(descriptor, dict) else descriptor)
    column = descriptor.get("column") if isinstance(descriptor, dict) else None
    if path.suffix == ".json":
        values = json.loads(path.read_text())
        if isinstance(values, dict):
            values = values[column or "cell_ids"]
        return [str(value) for value in values]
    if column:
        frame = pd.read_csv(path, sep=descriptor.get("separator", "\t"), dtype=str)
        return frame[column].tolist()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as handle:
        return [line.strip() for line in handle if line.strip()]


def decode(values):
    return [value.decode() if isinstance(value, bytes) else str(value) for value in values]


def load_matrix(descriptor):
    path = descriptor["path"]
    kind = descriptor["format"]
    if kind == "h5ad":
        return ad.read_h5ad(path, backed="r")
    if kind == "10x_h5":
        with h5py.File(path, "r") as handle:
            matrix = handle["matrix"]
            values = sparse.csc_matrix(
                (matrix["data"][:], matrix["indices"][:], matrix["indptr"][:]),
                shape=tuple(matrix["shape"][:]),
            ).T.tocsr()
            obs = pd.DataFrame(index=decode(matrix["barcodes"][:]))
            features = matrix["features"]
            var = pd.DataFrame(index=decode(features["id"][:]))
            var["gene_symbol"] = decode(features["name"][:])
            if "feature_type" in features:
                var["feature_type"] = decode(features["feature_type"][:])
        data = ad.AnnData(values, obs=obs, var=var)
        if "feature_type" in var:
            data = data[:, data.var["feature_type"] == "Gene Expression"].copy()
        return data
    if kind == "mtx":
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rb") as handle:
            values = io.mmread(handle).tocsr()
        if descriptor.get("orientation", "genes_by_cells") == "genes_by_cells":
            values = values.T.tocsr()
        barcodes = pd.read_csv(descriptor["barcodes_path"], sep="\t", header=None, dtype=str)
        genes = pd.read_csv(descriptor["genes_path"], sep="\t", header=None, dtype=str)
        var = pd.DataFrame(index=genes[0].tolist())
        var["gene_symbol"] = genes[1].tolist() if genes.shape[1] > 1 else genes[0].tolist()
        if values.shape != (len(barcodes), len(genes)):
            raise ValueError("Matrix dimensions do not match barcode and gene files")
        return ad.AnnData(values, obs=pd.DataFrame(index=barcodes[0].tolist()), var=var)
    raise ValueError(f"Unsupported matrix format {kind}")


def read_record(record, require_verified=True):
    if require_verified and record.get("status") != "verified":
        raise ValueError("This record has unresolved linkage and cannot be loaded as verified")
    perturbed = cell_ids(record["contrast"]["perturbed_cells"])
    controls = cell_ids(record["contrast"]["control_cells"])
    if not perturbed or not controls:
        raise ValueError("Both comparison groups must contain actual cells")
    if len(set(perturbed)) != len(perturbed) or len(set(controls)) != len(controls):
        raise ValueError("Duplicate cell IDs in a comparison group")
    if set(perturbed) & set(controls):
        raise ValueError("Perturbed and control cells overlap")
    for values, key in [(perturbed, "n_perturbed"), (controls, "n_control")]:
        expected = record["contrast"].get(key)
        if expected is not None and len(values) != expected:
            raise ValueError(f"Cell list length differs from {key}")
    matrix = load_matrix(record["matrix"])
    try:
        if not matrix.obs_names.is_unique:
            raise ValueError("Matrix cell IDs are not unique")
        requested = perturbed + controls
        positions = matrix.obs_names.get_indexer(requested)
        if (positions < 0).any():
            raise ValueError(f"Missing {(positions < 0).sum()} referenced cell IDs")
        selected = matrix[positions]
        selected = selected.to_memory() if selected.isbacked else selected.copy()
        target_column = next((name for name in ["target_gene", "target", "gene"] if name in selected.obs), None)
        if target_column:
            observed = selected.obs.iloc[:len(perturbed)][target_column].astype(str)
            if not observed.eq(record["perturbation"]["target_gene"]).all():
                raise ValueError("Perturbed cell annotations do not match the target gene")
        control_checked = "is_control" in selected.obs
        if control_checked:
            labels = selected.obs["is_control"]
            if not labels.iloc[:len(perturbed)].eq(False).all() or not labels.iloc[len(perturbed):].eq(True).all():
                raise ValueError("Cell lists contradict matrix control annotations")
        context_checks = {}
        for field in ["gem_group", "gemgroup", "sample", "cell_line"]:
            expected = record["context"].get(field)
            if expected is not None and field in selected.obs:
                if not selected.obs[field].astype(str).eq(str(expected)).all():
                    raise ValueError(f"Cell annotations do not match context field {field}")
                context_checks[field] = True
        layer = record["matrix"].get("layer")
        if layer and layer != "X":
            selected.X = selected.layers[layer].copy()
        selected.obs["comparison_group"] = ["perturbed"] * len(perturbed) + ["control"] * len(controls)
        selected.uns["record_id"] = record["record_id"]
        selected.uns["source_matrix"] = record["matrix"]["path"]
        selected.uns["reader_checks"] = {
            "target_annotation_checked": target_column is not None,
            "control_annotation_checked": control_checked,
            "context_fields_checked": context_checks,
        }
        return selected
    finally:
        if matrix.isbacked:
            matrix.file.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", required=True)
    parser.add_argument("--record", required=True)
    parser.add_argument("--output", help="Optional output H5AD containing selected cells")
    args = parser.parse_args()
    payload = json.loads(Path(args.records).read_text(encoding="utf-8-sig"))
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    matches = [record for record in records if record["record_id"] == args.record]
    if len(matches) != 1:
        raise ValueError("Expected exactly one record with this ID")
    selected = read_record(matches[0])
    if args.output:
        selected.write_h5ad(args.output, compression="gzip")
    sample = selected.X[:3, :5]
    sample = sample.toarray() if sparse.issparse(sample) else np.asarray(sample)
    print(json.dumps({"record_id": args.record, "shape": list(selected.shape),
                      "groups": selected.obs["comparison_group"].value_counts().to_dict(),
                      "cell_ids": selected.obs_names[:3].tolist(),
                      "gene_ids": selected.var_names[:5].tolist(), "values": sample.tolist()}, ensure_ascii=False))
