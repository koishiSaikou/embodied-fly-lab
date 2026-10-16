"""Export exact model-index classification for the read-only activity dashboard."""
import json
from pathlib import Path

from neuron_annotations import annotations
from prepare_annotations import MODEL_FILE, TABLE_FILE, sha256

ROOT = Path(__file__).resolve().parent
CLASS_STYLE = [
    ('optic', '视叶内部', '#73b9ff'),
    ('visual_projection', '视觉投射', '#a49aff'),
    ('visual_centrifugal', '视觉离心', '#d795ef'),
    ('sensory', '感觉输入', '#f2c16e'),
    ('sensory_ascending', '感觉 / 上行', '#d8d979'),
    ('central', '中央神经元', '#73dbb5'),
    ('descending', '下行神经元', '#ff9c7a'),
    ('ascending', '上行神经元', '#6ed4dc'),
    ('motor', '运动神经元', '#f78eb4'),
    ('endocrine', '内分泌', '#b5c589'),
    ('__unknown_class__', '大类未知', '#a4adb9'),
]


def main():
    store = annotations()
    if not store.summary['available']:
        raise RuntimeError('Import annotations first')
    classes = [dict(key=key, label=label, color=color, population=0)
               for key, label, color in CLASS_STYLE]
    class_lookup = {row['key']: i for i, row in enumerate(classes)}
    names = sorted({row['cell_type'] for row in store.rows if row['cell_type']})
    types = [dict(key=name, label=name, population=0) for name in names]
    types.append(dict(key='__unknown_type__', label='类型未知', population=0))
    type_lookup = {row['key']: i for i, row in enumerate(types)}
    class_of, type_of, ids = [], [], []
    for index, row in enumerate(store.rows):
        assert row['index'] == index and isinstance(row['flywire_id'], str)
        cls = class_lookup[row['super_class'] or '__unknown_class__']
        typ = type_lookup[row['cell_type'] or '__unknown_type__']
        classes[cls]['population'] += 1
        types[typ]['population'] += 1
        class_of.append(cls)
        type_of.append(typ)
        ids.append(row['flywire_id'])
    assert sum(row['population'] for row in classes) == len(ids) == 138639
    assert classes[-1]['population'] == 14 and types[-1]['population'] == 1492
    catalog = dict(schema_version=1, materialization=783, model_nodes=len(ids),
                   annotation_version=store.summary['annotation_version'],
                   source_sha256=store.summary['source_sha256'],
                   model_ids_sha256=sha256(MODEL_FILE), annotation_table_sha256=sha256(TABLE_FILE),
                   classes=classes, types=types, class_of=class_of, type_of=type_of, ids=ids)
    path = ROOT / 'web' / 'activity_catalog.json'
    path.write_text(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(json.dumps(dict(path=str(path), bytes=path.stat().st_size, nodes=len(ids),
                          classes=len(classes), named_types=len(names), unknown_types=1492)))


if __name__ == '__main__':
    main()
