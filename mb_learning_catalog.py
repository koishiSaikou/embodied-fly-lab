"""Read-only local connectome preflight; no torch, simulation or supplied paths."""
from functools import lru_cache
from pathlib import Path

from mb_learning import validate_controls, resolve_roles, cue_patterns, validate_memory, MAX_MEMORY_EDGES
from neuron_annotations import annotations

ROOT=Path(__file__).resolve().parent
CONNECTIONS=ROOT/'brain-model/data/2025_Connectivity_783.parquet'


@lru_cache(maxsize=16)
def _selected_edges(kc_type,mbon_type,stamp,size):
    import pyarrow.parquet as pq
    controls=validate_controls(dict(kc_type=kc_type,mbon_type=mbon_type))
    rows=annotations().rows;roles=resolve_roles(controls,rows)
    kc=set(roles['kc_indices'])
    table=pq.read_table(CONNECTIONS,columns=['Presynaptic_Index','Postsynaptic_Index','Excitatory x Connectivity'],
                        filters=[('Postsynaptic_Index','in',roles['mbon_indices'])])
    edges=[];excluded=0
    for pre,post,weight in zip(*(table[name].to_pylist() for name in table.column_names)):
        if pre not in kc:continue
        if weight<=0:excluded+=1;continue
        edges.append(dict(pre_id=rows[pre]['flywire_id'],post_id=rows[post]['flywire_id']))
        if len(edges)>MAX_MEMORY_EDGES:raise ValueError('所选类型超过 30000 条可塑连接，请缩小类型范围')
    if not edges:raise ValueError('所选类型没有既有正权重 KC→MBON 连接')
    if len({(e['pre_id'],e['post_id']) for e in edges})!=len(edges):
        raise ValueError('连接表含重复 KC→MBON 对，不能按精确边标识导入记忆')
    return edges,excluded


def allowed_edges(controls):
    controls=validate_controls(controls)
    stat=CONNECTIONS.stat()
    edges,_=_selected_edges(controls['kc_type'],controls['mbon_type'],stat.st_mtime_ns,stat.st_size)
    return [dict(edge) for edge in edges]


def preview_selection(controls):
    controls=validate_controls(controls);rows=annotations().rows
    roles=resolve_roles(controls,rows);stat=CONNECTIONS.stat()
    edges,excluded=_selected_edges(controls['kc_type'],controls['mbon_type'],stat.st_mtime_ns,stat.st_size)
    patterns=cue_patterns(controls,rows,roles)
    return dict(controls=controls,kc_count=len(roles['kc_indices']),mbon_count=len(roles['mbon_indices']),
                positive_edges=len(edges),edge_count=len(edges),excluded_nonpositive_edges=excluded,
                role_counts=roles['class_counts'],patterns=patterns['statistics'],
                mbon_ids=[rows[i]['flywire_id'] for i in roles['mbon_indices']],
                warnings=['角色按 cell_class 识别，不能按 top_nt 预测代替；不改原连接符号。',
                          '本地连接表只做预检，实际运行以脑工作进程 CSR 再核对结果为准。',
                          '外部教学门控和确定性气味→KC输入为工程代理；无完整ORN→PN或DAN释放机制。'])


def validate_local_memory(memory,controls):
    if memory is None:return None
    return validate_memory(memory,controls,allowed_edges(controls))
