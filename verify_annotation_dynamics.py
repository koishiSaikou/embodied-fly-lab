"""Compare captured spikes with/without display labels; keep NT source groups fixed."""
import gc
import json
from pathlib import Path
from types import SimpleNamespace

import brain_worker

ROOT = Path(__file__).resolve().parent
real_annotations = brain_worker.annotations
results = {}
for label in ('without_annotations', 'with_annotations'):
    brain_worker.annotations = (real_annotations if label == 'with_annotations' else
                               lambda: SimpleNamespace(at=lambda _: None, summary={'available': False},
                                                       rows=real_annotations().rows))
    brain = brain_worker.Brain(seed=42)
    chunks = [brain.advance([0, 0], 100, capture_activity=True),
              brain.advance([100, 100], 100, capture_activity=True),
              brain.advance([100, 100], 100, capture_activity=True)]
    if label == 'with_annotations':
        assert brain.p9_ids == [720575940627652358, 720575940635872101]
        assert brain.metadata()['annotations']['typed_nodes'] == 137147
        assert any(n['annotation']['cell_type'] == 'DNp09'
                   for c in chunks for n in c['activity']['top_neurons'])
    for c in chunks:
        c.pop('chunk_wall_seconds')
        c.pop('vram_allocated_mb')
        for n in c['activity']['top_neurons']:
            n.pop('annotation')
    results[label] = chunks
    del brain
    gc.collect()
    brain_worker.torch.cuda.empty_cache()
assert results['without_annotations'] == results['with_annotations'], 'Labels changed neural results'
assert results['with_annotations'][-1]['total_spikes'] == 104
assert results['with_annotations'][-1]['active_neurons'] == 32
results['result'] = 'Identical spike events, voltages, rates, drives and counts with/without annotation lookup'
destination = ROOT / 'output' / 'annotations'
destination.mkdir(parents=True, exist_ok=True)
(destination / 'dynamics_regression.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps({'result': results['result'], 'total_spikes': 104, 'active_neurons': 32}, indent=2))
