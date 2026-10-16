"""Verify presynaptic NT gains, reversibility, baseline equivalence and CUDA use."""
import argparse
import gc
import hashlib
import json
from pathlib import Path

import pyarrow  # Import before torch on Windows.
import torch
from transmitter_modulation import TransmitterModulation, defaults

ROOT=Path(__file__).resolve().parent


def check_cpu():
    indices=torch.tensor([[0,0,1,2,3,4],[1,4,0,2,3,1]])
    values=torch.tensor([-3.,7.,5.,2.,-4.,-6.])
    base=torch.sparse_coo_tensor(indices,values,(5,5)).to_sparse_csr()
    original=base.values().clone()
    rows=[{'top_nt':nt} for nt in ('acetylcholine','gaba','glutamate','dopamine',None)]
    modulation=TransmitterModulation(base,rows)
    assert modulation.weights is base
    assert modulation.apply(defaults()) is base
    adjusted=modulation.apply(dict(acetylcholine=.5,gaba=0,glutamate=2,dopamine=2))
    expected=base.to_dense()*torch.tensor([.5,0.,2.,2.,1.])[None,:]
    assert torch.equal(adjusted.to_dense(),expected), 'Scale source columns, never destination rows'
    spikes=torch.tensor([[1.,2.,3.,4.,5.]])
    assert torch.equal(spikes@adjusted.transpose(0,1),spikes@expected.transpose(0,1))
    assert adjusted.to_dense()[0,4]==7, 'Unknown source remains at baseline'
    assert adjusted.to_dense()[3,3]==-8, 'Preserve base sign, even for a dopamine prediction'
    assert torch.equal(base.values(),original)
    for invalid in ({'unknown':1},{'gaba':-1},{'gaba':float('nan')},{'dopamine':True},{}):
        before=dict(modulation.gains)
        try:modulation.apply(invalid)
        except ValueError:pass
        else:raise AssertionError(invalid)
        assert modulation.gains==before and modulation.weights is adjusted
    assert modulation.apply(defaults()) is base
    assert torch.equal(base.values(),original)
    meta=modulation.metadata()
    assert sum(g['neurons'] for g in meta['groups'])==5
    assert sum(g['weighted_edges'] for g in meta['groups'])==6
    return dict(presynaptic_axis=True,signs_preserved=True,unknown_fixed=True,
                original_tensor_unchanged=True,restore_exact_base_object=True,invalid_patch_atomic=True)


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def check_gpu():
    from brain_worker import Brain
    paths=[ROOT/'brain-model/data'/name for name in ('weight_coo.pkl','weight_csr.pkl','neuron_annotations.parquet')]
    paths=[path for path in paths if path.is_file()]
    before={str(path):digest(path) for path in paths}
    a=Brain(seed=42)
    meta=a.metadata()['transmitter_modulation']
    assert sum(g['neurons'] for g in meta['groups'])==138639
    assert sum(g['weighted_edges'] for g in meta['groups'])==15091983
    assert next(g for g in meta['groups'] if g['key']=='__unknown__')['neurons']==293
    assert a.model.weights is a.transmitters.base_weights
    baseline=a.advance([0,0],100,transmitter_gains=defaults())
    reference=a.advance([100,100],200,transmitter_gains=defaults())
    assert baseline['total_spikes']==0 and reference['total_spikes']==104 and reference['active_neurons']==32
    reference_state=[part.cpu().clone() for part in a.state]
    del a;gc.collect();torch.cuda.empty_cache()
    b=Brain(seed=42)
    b.advance([0,0],100)
    blocked=b.advance([100,100],200,transmitter_gains={'acetylcholine':0})
    assert blocked['transmitter_gains']['acetylcholine']==0
    assert not all(torch.equal(part,other.cpu()) for part,other in zip(reference_state,b.state))
    assert blocked['total_spikes']!=reference['total_spikes'], 'Expected a visible network response in this fixed probe'
    source_mask=b.transmitters.edge_groups==0
    assert torch.count_nonzero(b.model.weights.values()[source_mask])==0
    assert torch.equal(b.model.weights.values()[~source_mask],b.transmitters.base_weights.values()[~source_mask])
    current_ms=b.sim_ms
    restored=b.advance([0,0],20,transmitter_gains=defaults())
    assert b.model.weights is b.transmitters.base_weights and b.sim_ms==current_ms+20
    assert restored['total_spikes']>=blocked['total_spikes']
    after={str(path):digest(path) for path in paths}
    assert before==after
    return dict(default_spikes=reference['total_spikes'],default_active=reference['active_neurons'],
                acetylcholine_block_spikes=blocked['total_spikes'],full_neural_state_changed=True,
                source_only_scaling=True,live_restore_without_time_reset=True,source_file_hashes_unchanged=True,
                groups=meta['groups'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu',action='store_true')
    args=parser.parse_args()
    result=dict(status='passed',cpu=check_cpu())
    if args.gpu:result['gpu']=check_gpu()
    output=ROOT/'output/transmitter_controls'
    output.mkdir(parents=True,exist_ok=True)
    (output/('gpu.json' if args.gpu else 'cpu.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
