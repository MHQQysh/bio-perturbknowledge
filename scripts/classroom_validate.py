"""Independently verify response tables and export aligned model arrays."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--origin", choices=["local", "remote"], required=True)
    args = parser.parse_args()
    root = args.run
    records = json.loads((root / "records.json").read_text(encoding="utf-8"))["records"]
    hashes = {}
    row_gene_ids = None
    row_control_ids = None
    baselines, deltas, examples = [], [], []
    manifest = "response-sha256.txt" if args.origin == "local" else "remote-sha256.txt"
    with (root / manifest).open(encoding="utf-8-sig") as file:
        for line in file:
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                hashes[parts[1]] = parts[0]
    for row, record in enumerate(records):
        rid = record["record_id"]
        path = root / f"{rid}_response.csv"
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes.get(path.name):
            raise ValueError(f"Response file does not match the run fingerprint: {path.name}")
        data = pd.read_csv(path, dtype={"gene_id": str})
        gene_ids = data["gene_id"].tolist()
        control_ids = record["contrast"]["control_cells"]
        if row == 0:
            row_gene_ids, row_control_ids = gene_ids, control_ids
        if not data["gene_id"].is_unique or gene_ids != row_gene_ids or control_ids != row_control_ids:
            raise ValueError("Gene order or shared control membership differs")
        values = data.drop(columns="gene_id").to_numpy()
        if not np.isfinite(values).all():
            raise ValueError("Non-finite expression values in response")
        np.testing.assert_allclose(values[:, 0] - values[:, 1], values[:, 2], atol=1e-12, rtol=1e-10)
        if baselines:
            np.testing.assert_allclose(values[:, 1], baselines[0], atol=1e-12, rtol=1e-10)
        baselines.append(values[:, 1])
        deltas.append(values[:, 2])
        examples.append({
            "record_id": rid, "split_group": "GSE142078_GSM4219576_shared_NTC",
            "Input": {"target_gene": record["perturbation"]["target_gene"], "cell_line": "LUHMES",
                      "baseline_array": "X_control_mean_log1p_cp10k", "row": row},
            "Target": {"response_array": "Y_delta_mean_log1p_cp10k", "row": row},
        })
    x, y = np.stack(baselines), np.stack(deltas)
    array_path = root / "lalli-model-arrays.npz"
    np.savez_compressed(array_path, gene_ids=np.asarray(row_gene_ids, dtype=str),
                        X_control_mean_log1p_cp10k=x, Y_delta_mean_log1p_cp10k=y)
    with np.load(array_path, allow_pickle=False) as restored:
        np.testing.assert_array_equal(restored["X_control_mean_log1p_cp10k"], x)
        np.testing.assert_array_equal(restored["Y_delta_mean_log1p_cp10k"], y)
        np.testing.assert_array_equal(restored["gene_ids"], row_gene_ids)
    allowed = {"target_gene", "cell_line", "baseline_array", "row"}
    if not all(set(example["Input"]) == allowed for example in examples):
        raise ValueError("Unexpected Input keys")
    validation = {"status": "passed", "csv_sha256_matched": len(examples), "fingerprint_origin": args.origin,
                  "gene_delta_recomputed": True, "shared_control_ids_match": True,
                  "input_keys": sorted(allowed), "array_shape": list(y.shape),
                  "independent_conditions": False, "examples": examples}
    if args.origin == "remote":
        validation["remote_csv_sha256_matched"] = len(examples)
    (root / "lalli-validation.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(validation))


if __name__ == "__main__":
    main()
