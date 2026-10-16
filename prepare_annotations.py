"""Join pinned FlyWire v783 annotations to the model's exact tensor order."""
import csv
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'brain-model' / 'data'
CACHE = ROOT / 'downloads' / 'annotations'
VERSION = 'v3.2.0'
COMMIT = 'a83b2776d60d5764cef36b927f5f9679c16c47a2'
SOURCE_URL = f'https://raw.githubusercontent.com/flyconnectome/flywire_annotations/{COMMIT}/supplemental_files/Supplemental_file1_neuron_annotations.tsv'
SOURCE_SHA256 = 'b214970b55d2fbe0853bba536fdcb9e28730f4eb7ab06f600491df795da683cd'
MODEL_FILE = DATA / '2025_Completeness_783.csv'
TABLE_FILE = DATA / 'neuron_annotations.parquet'
REPORT_FILE = DATA / 'annotation_report.json'
P9 = ['720575940627652358', '720575940635872101']


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def model_ids():
    with MODEL_FILE.open(newline='', encoding='utf-8') as stream:
        rows = csv.reader(stream)
        next(rows)
        identifiers = [r[0] for r in rows]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Duplicate model IDs')
    return identifiers


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    raw = CACHE / f'flywire_v783_annotations_{VERSION}.tsv'
    if not raw.exists():
        temporary = raw.with_suffix('.download')
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, temporary.open('wb') as target:
            while block := response.read(1024 * 1024):
                target.write(block)
        if sha256(temporary) != SOURCE_SHA256:
            raise ValueError('Downloaded annotation checksum mismatch')
        temporary.replace(raw)
    if sha256(raw) != SOURCE_SHA256:
        raise ValueError('Cached annotation checksum mismatch')
    with raw.open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        columns = list(reader.fieldnames)
        source = {}
        for row in reader:
            identifier = row['root_id']
            if not identifier.isdecimal() or len(identifier) != 18 or identifier in source:
                raise ValueError(f'Invalid or duplicate root ID: {identifier}')
            if row['top_nt_conf'] and not 0 <= float(row['top_nt_conf']) <= 1:
                raise ValueError(f'Invalid transmitter confidence: {identifier}')
            source[identifier] = row
    ids = model_ids()
    records = []
    for index, identifier in enumerate(ids):
        original = source.get(identifier)
        record = {key: (original[key] or None) if original else None for key in columns}
        record.update(root_id=identifier, neuron_index=index,
                      annotation_status='matched' if original else 'unmatched')
        records.append(record)
    frame = pd.DataFrame(records)
    frame['neuron_index'] = frame['neuron_index'].astype('int32')
    # All source fields remain strings: no lossy floating-point IDs or imputed labels.
    temporary = TABLE_FILE.with_suffix('.parquet.tmp')
    frame.to_parquet(temporary, index=False, compression='zstd')
    temporary.replace(TABLE_FILE)
    matched = [r for r in records if r['annotation_status'] == 'matched']
    unknown = [{'neuron_index': r['neuron_index'], 'root_id': r['root_id'],
                'reason': 'missing_annotation_row' if r['annotation_status'] == 'unmatched' else 'empty_cell_type',
                'super_class': r['super_class'], 'hemibrain_type': r['hemibrain_type']}
               for r in records if not r['cell_type']]
    pd.DataFrame(unknown).to_csv(DATA / 'unknown_neuron_types.csv', index=False, encoding='utf-8-sig')
    model_set = set(ids)
    source_only = [identifier for identifier in source if identifier not in model_set]
    pd.DataFrame({'root_id': source_only}).to_csv(DATA / 'annotations_outside_model.csv', index=False)
    channels = []
    for channel, identifier in zip(('A', 'B'), P9):
        row = source[identifier]
        channels.append({'channel': channel, 'root_id': identifier, 'cell_type': row['cell_type'],
                         'upstream_channel_name': 'left' if channel == 'A' else 'right',
                         'annotation_side': row['side']})
    report = {
        'schema_version': 1, 'materialization': 783, 'annotation_version': VERSION,
        'annotation_commit': COMMIT, 'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
        'imported_at_utc': datetime.now(timezone.utc).isoformat(),
        'model_ids_sha256': sha256(MODEL_FILE), 'table_sha256': sha256(TABLE_FILE),
        'source_rows': len(source), 'duplicate_root_ids': 0, 'model_nodes': len(ids),
        'matched_rows': len(matched), 'unmatched_rows': len(ids) - len(matched),
        'unmatched_ids': [r['root_id'] for r in records if r['annotation_status'] == 'unmatched'],
        'typed_nodes': sum(bool(r['cell_type']) for r in records), 'unknown_type_nodes': len(unknown),
        'matched_without_cell_type': sum(not r['cell_type'] for r in matched),
        'source_only_rows': len(source_only),
        'field_coverage': {key: sum(bool(r[key]) for r in records) for key in columns},
        'usable_side_nodes': sum(r['side'] in ('left', 'right', 'center') for r in records),
        'counts': {key: dict(Counter(r[key] for r in records if r[key]).most_common())
                   for key in ('flow', 'super_class', 'cell_class', 'cell_type', 'top_nt')},
        'p9_channels': channels,
        'warnings': [
            'P9 upstream channel names disagree with annotation anatomical side. Preserve A/B motor order; do not swap.',
            'Labels only. Uniform LIF equations, original connection signs/weights and sensory/motor mappings are unchanged.',
            'top_nt is a prediction; known_nt and known_nt_source are retained separately and may contain multiple entries.',
            'Hierarchy is not a per-neuropil innervation map. pos/soma are 4x4x40 nm voxel coordinates.',
            'No annotation row or empty cell_type remains unknown; hemibrain_type is not substituted for cell_type.'
        ],
        'weight_files_sha256': {p.name: sha256(p) for p in
                                 [DATA / 'weight_coo.pkl', DATA / 'weight_csr.pkl'] if p.exists()},
    }
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('model_nodes', 'matched_rows', 'typed_nodes',
                                            'unknown_type_nodes', 'unmatched_rows', 'source_only_rows', 'p9_channels')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
