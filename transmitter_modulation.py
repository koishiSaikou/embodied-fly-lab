"""Presynaptic predicted-transmitter gains, separate from original sparse caches.

W_eff[post, pre] = W_base[post, pre] * gain[top_nt(pre)].
No receptor, concentration, plasticity or sign inference is introduced here.
Torch is imported lazily so the body/web process can read the schema without it.
"""
from collections import Counter
import math

TRANSMITTERS = (
    ('acetylcholine', '乙酰胆碱'), ('glutamate', '谷氨酸'), ('gaba', 'GABA'),
    ('dopamine', '多巴胺'), ('serotonin', '血清素（5-HT）'), ('octopamine', '章鱼胺'),
)
UNKNOWN = '__unknown__'


def defaults():
    return {key: 1.0 for key, _ in TRANSMITTERS}


def validate_gains(changes, current=None):
    if not isinstance(changes, dict) or not changes:
        raise ValueError('transmitter_gains must be a nonempty object')
    result = dict(defaults() if current is None else current)
    for key, value in changes.items():
        if key not in result or key == UNKNOWN:
            raise ValueError('Unknown transmitter gain: ' + str(key))
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError('Transmitter gains must be finite numbers in [0,2]')
        result[key] = float(value)
    return result


def fields():
    return [dict(key='nt_'+key, label=label+'来源突触增益', unit='倍', group='transmitter',
                 minimum=0, maximum=2, step=.1,
                 help='按突触前神经元的 top_nt 预测分组；0 阻断后续输出，1 保持原值，2 放大两倍，保留原权重正负号。')
            for key, label in TRANSMITTERS]


class TransmitterModulation:
    def __init__(self, weights, rows):
        import torch
        if weights.layout != torch.sparse_csr or weights.shape != (len(rows),len(rows)):
            raise ValueError('Transmitter modulation requires model-order rows and square CSR weights')
        self.base_weights = weights
        self.gains = defaults()
        self.weights = weights
        self.keys = [key for key, _ in TRANSMITTERS] + [UNKNOWN]
        code = {key: index for index, key in enumerate(self.keys)}
        labels = [str(row.get('top_nt') or '').lower() for row in rows]
        nodes = [code.get(label, code[UNKNOWN]) for label in labels]
        node_counts = Counter(nodes)
        groups = torch.tensor(nodes, dtype=torch.uint8, device=weights.device)
        # CSR columns index the presynaptic/source neuron, not the postsynaptic row.
        self.edge_groups = groups[weights.col_indices()]
        counts = torch.bincount(self.edge_groups.long(), minlength=len(self.keys)).cpu().tolist()
        positive = torch.bincount(self.edge_groups[weights.values()>0].long(), minlength=len(self.keys)).cpu().tolist()
        negative = torch.bincount(self.edge_groups[weights.values()<0].long(), minlength=len(self.keys)).cpu().tolist()
        names = dict(TRANSMITTERS, **{UNKNOWN:'预测未知 / 未匹配（固定 1 倍）'})
        self.groups = [dict(key=key,label=names[key],neurons=node_counts[i],weighted_edges=counts[i],
                            positive_edges=positive[i],negative_edges=negative[i],
                            zero_edges=counts[i]-positive[i]-negative[i]) for i,key in enumerate(self.keys)]

    def apply(self, changes):
        import torch
        candidate = validate_gains(changes, self.gains)
        if candidate == self.gains:
            return self.weights
        if all(value == 1 for value in candidate.values()):
            effective = self.base_weights
        else:
            gain = torch.tensor([candidate[key] for key, _ in TRANSMITTERS]+[1.0],
                                dtype=self.base_weights.dtype,device=self.base_weights.device)
            values = self.base_weights.values() * gain[self.edge_groups.long()]
            effective = torch.sparse_csr_tensor(self.base_weights.crow_indices(),self.base_weights.col_indices(),
                                               values,size=self.base_weights.shape,device=self.base_weights.device)
        # Commit after the entire candidate is validated and constructed; never edit the base tensor.
        self.weights, self.gains = effective, candidate
        return effective

    def metadata(self):
        return dict(group_source='model-order annotation top_nt prediction', groups=self.groups,
                    gains=dict(self.gains),unknown_gain=1.0,
                    formula='W_eff[post,pre] = W_base[post,pre] * gain[top_nt(pre)]',
                    preserves_base_weight_sign=True,original_cache_unchanged=True,
                    interpretation='Simplified outgoing-connection gain, not concentration/receptor dynamics or reward learning')
