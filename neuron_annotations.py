"""Read-only annotation lookup, independent of neural dynamics and random state."""
from functools import lru_cache
import json

import pandas as pd
from prepare_annotations import TABLE_FILE, REPORT_FILE, MODEL_FILE, model_ids, sha256

FIELDS = ('cell_type', 'hemibrain_type', 'super_class', 'cell_class', 'cell_sub_class',
          'supertype', 'flow', 'side', 'top_nt', 'top_nt_conf', 'known_nt',
          'known_nt_source', 'synonyms', 'annotation_status')


class AnnotationStore:
    def __init__(self):
        self.rows, self.by_id, self.search_text = [], {}, []
        if not TABLE_FILE.exists() or not REPORT_FILE.exists():
            self.summary = {'available': False, 'reason': 'Run prepare_annotations.py to import annotations'}
            return
        report = json.loads(REPORT_FILE.read_text(encoding='utf-8'))
        if report['model_ids_sha256'] != sha256(MODEL_FILE) or report['table_sha256'] != sha256(TABLE_FILE):
            raise ValueError('Annotation/model checksum mismatch; rerun prepare_annotations.py')
        frame = pd.read_parquet(TABLE_FILE, columns=['root_id', 'neuron_index', *FIELDS]).fillna('')
        ids = model_ids()
        if frame.root_id.tolist() != ids or frame.neuron_index.tolist() != list(range(len(ids))):
            raise ValueError('Annotations must preserve the full model tensor order')
        for raw in frame.to_dict('records'):
            row = {key: raw[key] or None for key in FIELDS}
            row.update(flywire_id=raw['root_id'], index=int(raw['neuron_index']))
            row['top_nt_conf'] = float(row['top_nt_conf']) if row['top_nt_conf'] else None
            self.rows.append(row)
            self.by_id[row['flywire_id']] = row
            self.search_text.append(' '.join(str(row[key] or '') for key in
                                            ('flywire_id', 'cell_type', 'hemibrain_type', 'super_class',
                                             'cell_class', 'cell_sub_class', 'synonyms')).casefold())
        self.summary = {key: report[key] for key in
                        ('materialization', 'annotation_version', 'annotation_commit', 'source_url',
                         'source_sha256', 'model_nodes', 'matched_rows', 'unmatched_rows',
                         'typed_nodes', 'unknown_type_nodes', 'matched_without_cell_type',
                         'source_only_rows', 'field_coverage', 'usable_side_nodes', 'p9_channels', 'warnings')}
        self.summary.update(available=True, super_classes=sorted(report['counts']['super_class']),
                            predicted_transmitters=sorted(report['counts']['top_nt']))

    def get(self, identifier):
        return self.by_id.get(str(identifier))

    def at(self, index):
        return self.rows[index] if self.rows else None

    def search(self, q='', super_class='', nt='', unknown_only=False, limit=20, offset=0):
        q = q.strip().casefold()
        indices = [i for i, row in enumerate(self.rows)
                   if (not q or q in self.search_text[i])
                   and (not super_class or row['super_class'] == super_class)
                   and (not nt or row['top_nt'] == nt)
                   and (not unknown_only or not row['cell_type'])]
        return {'available': self.summary['available'], 'total': len(indices), 'offset': offset,
                'limit': limit, 'rows': [self.rows[i] for i in indices[offset:offset + limit]]}


@lru_cache(maxsize=1)
def annotations():
    return AnnotationStore()


def morphology_label(row, identifier):
    if row and row['cell_type']:
        side = {'left': '解剖左', 'right': '解剖右', 'center': '中线'}.get(row['side'], '侧别未知')
        return f"{row['cell_type']} · {side} · …{identifier[-6:]}"
    return '类型未知 · …' + identifier[-6:]
