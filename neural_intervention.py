"""Data-only virtual neuron interventions; no model imports or file access.

These are engineering Poisson inputs / LIF clamps, not calibrated optogenetic
actuators, receptor kinetics, or a model of tissue damage.
"""
from copy import deepcopy
import math
import re

MAX_TARGETS=10000
SUMMARY_LIMIT=20
UNSET=object()
_ID=re.compile(r'[0-9]{18}\Z')


def _finite(value,low,high,label):
    if type(value) not in (int,float) or not low<=value<=high or not math.isfinite(value):
        raise ValueError(label+'须为有效范围内的有限数值')
    return float(value)


def validate_protocol(value):
    """Normalize one scheduled protocol, or None to cancel without rollback."""
    if value is None:return None
    required={'mode','target','start_s','duration_s'}
    if not isinstance(value,dict) or set(value)-required-{'rate_hz'} or required-set(value):
        raise ValueError('神经干预协议字段无效')
    mode=value['mode']
    if mode not in ('stimulate','silence'):raise ValueError('神经干预模式须为 stimulate 或 silence')
    target=value['target']
    if not isinstance(target,dict):raise ValueError('神经干预目标须为对象')
    if target.get('kind')=='ids':
        ids=target.get('ids')
        if set(target)!={'kind','ids'} or not isinstance(ids,list) or not 1<=len(ids)<=MAX_TARGETS:
            raise ValueError('请提供 1–10000 个精确 FlyWire ID')
        if any(not isinstance(item,str) or not _ID.fullmatch(item) for item in ids):
            raise ValueError('FlyWire ID 必须是 18 位数字字符串，不能使用数字类型')
        target=dict(kind='ids',ids=list(dict.fromkeys(ids)))
    elif target.get('kind')=='cell_type':
        cell_type=target.get('value')
        if set(target)!={'kind','value'} or not isinstance(cell_type,str) or not cell_type or len(cell_type)>256 or not cell_type.isprintable():
            raise ValueError('cell_type 须为非空精确类型名称')
        target=dict(kind='cell_type',value=cell_type)
    else:raise ValueError('目标只能是精确 IDs 或 cell_type')
    start=_finite(value['start_s'],0,1e300,'开始模型秒')
    duration=_finite(value['duration_s'],.0001,3600,'干预持续模型秒')
    if start+duration<=start:raise ValueError('开始时间过大，无法表示所选时间窗')
    if mode=='stimulate' and 'rate_hz' not in value:raise ValueError('刺激须指定 rate_hz')
    rate=_finite(value.get('rate_hz',0),0,1000,'附加刺激 Hz')
    if mode=='silence' and rate!=0:raise ValueError('静默协议的 rate_hz 必须为 0')
    return dict(mode=mode,target=target,start_s=start,duration_s=duration,rate_hz=rate)


def resolve_targets(protocol,rows):
    """Return exact selected tensor indices in unchanged model order."""
    protocol=validate_protocol(protocol)
    if protocol is None:return []
    target=protocol['target'];requested=set(target['ids']) if target['kind']=='ids' else None
    result=[];found=set()
    for position,row in enumerate(rows):
        identifier=row.get('flywire_id')
        selected=identifier in requested if requested is not None else row.get('cell_type')==target['value']
        if not selected:continue
        if type(row.get('index')) is not int or row['index']!=position:
            raise ValueError('目标注释索引必须与模型行顺序完全一致')
        if not isinstance(identifier,str) or not _ID.fullmatch(identifier):raise ValueError('目标注释含无效 FlyWire ID')
        if identifier in found:raise ValueError('目标注释含重复 FlyWire ID')
        found.add(identifier);result.append(position)
        if len(result)>MAX_TARGETS:raise ValueError('单个协议最多选择 10000 个神经元')
    if requested is not None and found!=requested:
        missing=sorted(requested-found)
        raise ValueError('模型中不存在以下 FlyWire ID：'+', '.join(missing[:20]))
    if not result:raise ValueError('目标没有匹配任何模型神经元，请核对精确 cell_type 或 IDs')
    return result


def target_summary(protocol,rows,limit=SUMMARY_LIMIT):
    """Use limit=None for the full resolved list when persisting protocol setup."""
    normalized=validate_protocol(protocol);indices=resolve_targets(normalized,rows)
    if limit is not None and (type(limit) is not int or not 0<=limit<=MAX_TARGETS):raise ValueError('摘要上限无效')
    shown=indices if limit is None else indices[:limit]
    return dict(protocol=deepcopy(normalized),target_count=len(indices),
                target_ids=[rows[i]['flywire_id'] for i in shown],
                targets=[dict(index=i,flywire_id=rows[i]['flywire_id'],cell_type=rows[i].get('cell_type')) for i in shown],
                targets_truncated=len(shown)<len(indices))
