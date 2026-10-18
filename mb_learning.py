"""Data-only controls for a deliberately limited KC→MBON learning experiment.

Roles come from cell_class, never predicted transmitter identity. Teaching is
an external engineering gate; this does not reconstruct dopamine release.
"""
from collections import Counter
from dataclasses import asdict, dataclass, replace
import hashlib
import math
import re

MAX_MEMORY_EDGES=30000
ROLE_CLASSES={'kc':'Kenyon_Cell','mbon':'MBON','dan':'DAN'}
ID=re.compile(r'[0-9]{18}\Z')
FIELDS=[
    dict(key='enabled',kind='boolean',label='启用蘑菇体局部学习实验'),
    dict(key='plasticity_enabled',kind='boolean',label='允许局部突触变化'),
    dict(key='cue_input_enabled',kind='boolean',label='启用气味到 KC 的工程输入'),
    dict(key='kc_type',kind='text',label='精确 KC 类型'),
    dict(key='mbon_type',kind='text',label='精确 MBON 类型'),
    dict(key='learning_rate',minimum=0.,maximum=1.,label='LTD 速率 / 模型秒'),
    dict(key='eligibility_tau_s',minimum=.05,maximum=10.,label='资格迹衰减时间 / 模型秒'),
    dict(key='min_factor',minimum=.05,maximum=1.,label='最小局部因子'),
    dict(key='kc_input_hz',minimum=0.,maximum=200.,label='KC 气味输入 / Hz'),
    dict(key='coding_fraction',minimum=.01,maximum=.25,label='单气味稀疏编码比例'),
    dict(key='cue_seed',kind='integer',minimum=0,maximum=2**32-1,label='确定性气味编码种子'),
    dict(key='reward_source',kind='choice',choices=['manual','sugar_contact'],label='教学信号来源'),
]


def _number(value,low,high,label):
    if type(value) not in (int,float) or not low<=value<=high or not math.isfinite(value):
        raise ValueError(label+'必须是有效范围内的有限数值')
    return float(value)


@dataclass(frozen=True)
class MBLearningControls:
    enabled: bool=False
    plasticity_enabled: bool=True
    cue_input_enabled: bool=True
    kc_type: str='KCg-m'
    mbon_type: str='MBON01'
    learning_rate: float=.1
    eligibility_tau_s: float=1.
    min_factor: float=.2
    kc_input_hz: float=80.
    coding_fraction: float=.1
    cue_seed: int=1
    reward_source: str='manual'

    def snapshot(self):return asdict(self)

    def updated(self,changes):
        if not isinstance(changes,dict) or not changes:raise ValueError('请提供非空局部学习设置')
        specs={f['key']:f for f in FIELDS};normalized={}
        for key,value in changes.items():
            if key not in specs:raise ValueError('未知局部学习设置：'+str(key))
            spec=specs[key];kind=spec.get('kind')
            if kind=='boolean':
                if type(value) is not bool:raise ValueError(spec['label']+'必须是布尔开关')
            elif kind=='text':
                if not isinstance(value,str) or not value or len(value)>256 or not value.isprintable():
                    raise ValueError(spec['label']+'必须是非空精确名称')
            elif kind=='choice':
                if value not in spec['choices']:raise ValueError('教学信号来源无效')
            elif kind=='integer':
                if type(value) is not int or not spec['minimum']<=value<=spec['maximum']:
                    raise ValueError('气味编码种子必须是 0–2^32-1 的整数')
            else:value=_number(value,spec['minimum'],spec['maximum'],spec['label'])
            normalized[key]=value
        return replace(self,**normalized)


def validate_controls(value,current=None):
    controls=MBLearningControls() if current is None else MBLearningControls().updated(current)
    return controls.updated(value).snapshot()


def validate_cues(value):
    if value is None:return dict(sugar=0.,fruit=0.)
    if not isinstance(value,dict) or set(value)-{'sugar','fruit'}:raise ValueError('KC 气味输入只接受 sugar / fruit')
    return {key:_number(value.get(key,0.),0.,1.,'气味强度') for key in ('sugar','fruit')}


def validate_teaching(value):return _number(value,0.,1.,'外部教学信号')


def resolve_roles(controls,rows):
    controls=validate_controls(controls)
    counts=Counter(row.get('cell_class') for row in rows)
    kc=[];mbon=[]
    for index,row in enumerate(rows):
        role=row.get('cell_class');kind=row.get('cell_type')
        selected=(role==ROLE_CLASSES['kc'] and kind==controls['kc_type']) or (role==ROLE_CLASSES['mbon'] and kind==controls['mbon_type'])
        if not selected:continue
        if type(row.get('index')) is not int or row['index']!=index or not isinstance(row.get('flywire_id'),str) or not ID.fullmatch(row['flywire_id']):
            raise ValueError('KC/MBON 注释必须保留精确 ID 与模型顺序')
        (kc if role==ROLE_CLASSES['kc'] else mbon).append(index)
    if not kc or not mbon:raise ValueError('所选精确类型必须同时匹配 cell_class=Kenyon_Cell 的 KC 与 cell_class=MBON 的 MBON')
    return dict(kc_indices=kc,mbon_indices=mbon,
                class_counts={role:counts[name] for role,name in ROLE_CLASSES.items()},
                role_source='Exact cell_class, never top_nt; DAN count is descriptive only')


def cue_patterns(controls,rows,roles=None):
    controls=validate_controls(controls);roles=resolve_roles(controls,rows) if roles is None else roles
    indices=roles['kc_indices'];size=max(1,math.ceil(len(indices)*controls['coding_fraction']))
    patterns={}
    for odor in ('sugar','fruit'):
        def key(index):
            raw=f"mb-cue-v1|{controls['cue_seed']}|{odor}|{rows[index]['flywire_id']}".encode('ascii')
            return hashlib.sha256(raw).digest(),index
        chosen=set(sorted(indices,key=key)[:size])
        patterns[odor]=[index for index in indices if index in chosen]
    intersection=len(set(patterns['sugar'])&set(patterns['fruit']))
    union=2*size-intersection
    patterns['statistics']=dict(algorithm='sha256-rank-v1',seed=controls['cue_seed'],kc_count=len(indices),
        per_cue_count=size,actual_coding_fraction=size/len(indices),overlap_count=intersection,
        jaccard=intersection/union if union else 0.,uses_external_odor_values=False,
        interpretation='Deterministic engineering odor→KC bridge; not reconstructed ORN→PN connectivity')
    return patterns


def validate_memory_shape(value,controls,rows=None):
    """Validate portable data/roles without importing or reading a weight graph."""
    if value is None:return None
    controls=validate_controls(controls)
    if not isinstance(value,dict) or set(value)!={'schema_version','selection','edges'} or type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('不支持的局部学习记忆格式')
    if value['selection']!={key:controls[key] for key in ('kc_type','mbon_type')}:
        raise ValueError('记忆中的 KC / MBON 类型与当前选择不一致')
    edges=value['edges']
    if not isinstance(edges,list) or len(edges)>MAX_MEMORY_EDGES:raise ValueError('局部学习记忆最多 30000 条边')
    factors={}
    for edge in edges:
        if not isinstance(edge,dict) or set(edge)!={'pre_id','post_id','factor'}:raise ValueError('局部记忆边字段无效')
        pre,post=edge['pre_id'],edge['post_id']
        if any(not isinstance(item,str) or not ID.fullmatch(item) for item in (pre,post)):
            raise ValueError('记忆中的 ID 必须是 18 位字符串')
        pair=(pre,post)
        if pair in factors:raise ValueError('局部记忆含重复 KC→MBON 边')
        factors[pair]=_number(edge['factor'],controls['min_factor'],1.,'局部突触因子')
    if rows is not None:
        roles=resolve_roles(controls,rows)
        kc_ids={rows[i]['flywire_id'] for i in roles['kc_indices']}
        mbon_ids={rows[i]['flywire_id'] for i in roles['mbon_indices']}
        if any(pre not in kc_ids or post not in mbon_ids for pre,post in factors):
            raise ValueError('记忆边不属于所选精确 KC / MBON 角色与类型')
    return dict(schema_version=1,selection={key:controls[key] for key in ('kc_type','mbon_type')},
                edges=[dict(pre_id=pre,post_id=post,factor=factor) for (pre,post),factor in factors.items()])


def validate_memory(value,controls,allowed_edges):
    """Require the complete actual existing positive-edge set, in model order."""
    controls=validate_controls(controls)
    normalized=validate_memory_shape(value,controls)
    if normalized is None:raise ValueError('加载局部记忆需要完整 JSON 对象；清除请使用重置操作')
    expected={(edge['pre_id'],edge['post_id']) for edge in allowed_edges}
    if len(expected)!=len(allowed_edges) or len(expected)>MAX_MEMORY_EDGES:raise ValueError('本地可塑边集合无效')
    factors={(e['pre_id'],e['post_id']):e['factor'] for e in normalized['edges']}
    if set(factors)!=expected:raise ValueError('记忆必须完整覆盖当前所选的既有正 KC→MBON 边')
    return dict(schema_version=1,selection={key:controls[key] for key in ('kc_type','mbon_type')},
                edges=[dict(pre_id=e['pre_id'],post_id=e['post_id'],factor=factors[(e['pre_id'],e['post_id'])]) for e in allowed_edges])


def schema():return dict(fields=FIELDS,defaults=MBLearningControls().snapshot(),max_memory_edges=MAX_MEMORY_EDGES)
