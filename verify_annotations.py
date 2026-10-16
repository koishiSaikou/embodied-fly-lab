"""Integrity and HTTP integration checks for the model annotation join."""
import csv
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from neuron_annotations import annotations
from prepare_annotations import DATA, REPORT_FILE, model_ids, sha256
from habitat_monitor import HabitatMonitor
from activity_monitor import ActivityMonitor

ROOT = Path(__file__).resolve().parent


def check():
    store = annotations()
    report = json.loads(REPORT_FILE.read_text(encoding='utf-8'))
    ids = model_ids()
    assert len(store.rows) == len(ids) == 138639
    assert [r['flywire_id'] for r in store.rows] == ids
    assert all(isinstance(r['flywire_id'], str) and len(r['flywire_id']) == 18 for r in store.rows)
    assert store.search(unknown_only=True)['total'] == 1492
    assert sum(r['annotation_status'] == 'unmatched' for r in store.rows) == 14
    # Broad search also matches the separate hemibrain_type field (DNp71 here).
    assert sum(r['cell_type'] == 'DNp09' for r in store.search('dnp09')['rows']) == 2
    assert store.get('720575940627652358')['side'] == 'right'
    assert store.get('720575940635872101')['side'] == 'left'
    assert store.get('720575940624963786')['cell_type'] == 'LB4b'
    for identifier in report['unmatched_ids']:
        row = store.get(identifier)
        assert row['cell_type'] is None and row['annotation_status'] == 'unmatched'
    with (DATA / 'unknown_neuron_types.csv').open(encoding='utf-8-sig', newline='') as stream:
        unknown = list(csv.DictReader(stream))
    assert len(unknown) == 1492 and sum(r['reason'] == 'missing_annotation_row' for r in unknown) == 14
    assert all(0 <= r['top_nt_conf'] <= 1 for r in store.rows if r['top_nt_conf'] is not None)
    cache_paths=[DATA/name for name in ('weight_coo.pkl','weight_csr.pkl')]
    same_cache_bytes=all(path.exists() and sha256(path)==report['weight_files_sha256'].get(path.name) for path in cache_paths)
    if same_cache_bytes:
        cache_status='matches_original_cache_bytes'
    elif all(path.exists() for path in cache_paths):
        # Fresh pickle storage IDs differ across processes even for the same graph.
        completed=subprocess.run([str(ROOT/'.brain-venv/Scripts/python.exe'),str(ROOT/'verify_weight_cache.py')],
                                 cwd=ROOT,check=True,capture_output=True,text=True,encoding='utf-8')
        assert json.loads(completed.stdout)['status']=='passed'
        cache_status='rebuilt_caches_match_all_source_indices_and_values'
    else:
        cache_status='not_generated_yet'
    morphology = json.loads((ROOT / 'web/morphology.json').read_text(encoding='utf-8'))
    assert len(morphology['neurons']) == 16
    for n in morphology['neurons']:
        row = store.at(n['index'])
        assert row['flywire_id'] == n['id'] and n['annotation'] == row
        assert row['cell_type'] in n['label'] if row['cell_type'] else '未知' in n['label']

    checks = []
    for monitor in (HabitatMonitor(port=8475), ActivityMonitor(port=8476)):
        try:
            base = monitor.url.rstrip('/')
            def get(path):
                with urlopen(base + path, timeout=20) as response:
                    return json.loads(response.read())
            assert get('/api/annotations/summary')['typed_nodes'] == 137147
            result = get('/api/annotations/search?q=DNp09')
            assert {r['flywire_id'] for r in result['rows'] if r['cell_type'] == 'DNp09'} == {
                '720575940627652358', '720575940635872101'}
            assert get('/api/annotations/search?unknown_only=true')['total'] == 1492
            assert get('/api/annotations/search?q=720575940627652358')['rows'][0]['side'] == 'right'
            assert get('/api/annotations/search?super_class=descending&nt=acetylcholine')['total'] > 0
            assert get('/api/annotations/search?offset=20')['rows'][0]['index'] == 20
            assert get('/api/annotations/neuron/' + report['unmatched_ids'][0])['cell_type'] is None
            assert get('/api/annotations/report')['table_sha256'] == report['table_sha256']
            for path in ('/assets/annotations.js', '/assets/annotations.css', '/api/annotations/unknown.csv'):
                with urlopen(base + path) as response:
                    assert response.status == 200 and len(response.read()) > 100
            for path in ('/api/annotations/search?limit=101', '/api/annotations/search?offset=-1',
                         '/api/annotations/neuron/999'):
                try:
                    get(path)
                except HTTPError as exc:
                    assert exc.code in (400, 404, 422)
                else:
                    raise AssertionError('Invalid request accepted: ' + path)
            checks.append({'server': type(monitor).__name__, 'api_checks': 'passed'})
        finally:
            monitor.close()
    output = {'integrity': 'passed', 'model_nodes': len(ids), 'typed_nodes': 137147,
              'unknown_types': 1492, 'unmatched_ids': 14, 'weight_hashes_unchanged': same_cache_bytes,
              'weight_cache_status':cache_status,
              'http_checks': checks}
    destination = ROOT / 'output' / 'annotations'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'verification.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    check()
