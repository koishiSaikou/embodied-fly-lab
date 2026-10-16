"""Verify rebuilt sparse caches against the full signed connectivity table.

Pickle bytes contain process-dependent storage IDs, so equal matrices need not
have identical pickle hashes after relocation/rebuilding. Reads trusted caches.
"""
import hashlib
import json
import pickle
from pathlib import Path

import pyarrow  # Windows import order
import pandas as pd
import numpy as np
import torch

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'brain-model/data'


def main():
    with (DATA / 'weight_coo.pkl').open('rb') as stream:
        coo = pickle.load(stream)
    with (DATA / 'weight_csr.pkl').open('rb') as stream:
        csr = pickle.load(stream)
    frame = pd.read_parquet(DATA / '2025_Connectivity_783.parquet', columns=[
        'Postsynaptic_Index', 'Presynaptic_Index', 'Excitatory x Connectivity'])
    assert tuple(coo.shape) == tuple(csr.shape) == (138639, 138639)
    assert coo._nnz() == csr._nnz() == len(frame) == 15091983
    np.testing.assert_array_equal(coo._indices()[0].numpy(), frame['Postsynaptic_Index'].to_numpy())
    np.testing.assert_array_equal(coo._indices()[1].numpy(), frame['Presynaptic_Index'].to_numpy())
    np.testing.assert_array_equal(coo._values().numpy(), frame['Excitatory x Connectivity'].to_numpy(dtype='float32'))
    expected = coo.to_sparse_csr()
    for a, b in ((csr.crow_indices(), expected.crow_indices()),
                 (csr.col_indices(), expected.col_indices()), (csr.values(), expected.values())):
        assert torch.equal(a, b)
    fingerprint = hashlib.sha256()
    for array in (coo._indices().numpy(), coo._values().numpy()):
        fingerprint.update(memoryview(array).cast('B'))
    report = dict(status='passed', nodes=138639, weighted_edges=15091983,
                  all_coo_indices_and_signed_values_match_source=True, csr_matches_coo=True,
                  tensor_sha256=fingerprint.hexdigest())
    destination = ROOT / 'output/annotations'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'weight_cache_verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
