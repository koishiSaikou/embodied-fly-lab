"""Bounded, read-only model-time observations; never changes the neural model."""
from collections import deque
from copy import deepcopy
import math
import json
from pathlib import Path
import threading

CAPACITY=6000


def _number(value):
    return type(value) in (int,float) and math.isfinite(value)


def _signature(point,comparison=False):
    controls=dict(point['controls'])
    if comparison:controls.pop('plasticity_enabled',None)
    context=point.get('context',{})
    return dict(controls=controls,reset_revision=point['reset_revision'],
        selection_revision=point['selection_revision'],pattern_revision=point['pattern_revision'],
        mbon_ids=[item['flywire_id'] for item in point['mbon']],
        background={key:context.get(key) for key in ('arena_revision','behavior_revision','intervention_revision',
            'brain_controls','sensory_switches','disconnect_brain_output','intervention_state')})


def _weighted(points,getter,duration):
    values=[(getter(p),p['chunk_s']) for p in points]
    if any(not _number(value) for value,_ in values):return None
    return sum(value*dt for value,dt in values)/duration


def compare_windows(before,after):
    if before is None or after is None:return None
    reasons=[];warnings=[]
    if before['comparison_signature']!=after['comparison_signature']:
        reasons.append('目标、编码、局部记忆重置或背景控制不同；不能直接比较为同一条件的学习效应。')
    if before['end_s']>after['start_s']+1e-8:
        reasons.append('两个观察窗重叠或顺序颠倒；请先记录训练前，再记录之后的独立窗口。')
    if abs(before['duration_s']-after['duration_s'])>1e-7:
        warnings.append('两个实际窗口时长不同；放电率使用各自真实时长归一化。')
    for cue,label in (('sugar','糖气味'),('fruit','果香')):
        if abs(before['mean']['cues'][cue]-after['mean']['cues'][cue])>.05:
            warnings.append(label+'平均输入不同，响应差可能包含输入差异。')
    if abs(before['mean']['teaching_signal']-after['mean']['teaching_signal'])>.01:
        warnings.append('教学信号不同；建议在停止教学、冻结可塑性后比较响应。')
    for key in ('p9_rate_hz','sugar_rate_hz'):
        a=before['input_mean'].get(key);b=after['input_mean'].get(key)
        if a is not None and b is not None and a!=b:
            warnings.append('实际 '+key+' 输入不同，其他脑输入可能影响响应。')
    if before['signature']['controls'].get('plasticity_enabled') or after['signature']['controls'].get('plasticity_enabled'):
        warnings.append('至少一个窗口仍允许可塑性更新；这是活动中的观察性比较。')
    warnings.append('条件相符只代表记录参数可比较，不证明差异完全由学习引起；脑/身体初态和随机输入仍可能不同。')
    delta=None
    if not reasons:
        delta={key:after['mean'][key]-before['mean'][key] for key in ('kc_hz','mbon_hz','eligibility_mean','factor_mean','teaching_signal')}
        delta['cues']={key:after['mean']['cues'][key]-before['mean']['cues'][key] for key in ('sugar','fruit')}
        prior={item['flywire_id']:item for item in before['mbon']}
        delta['mbon']=[dict(flywire_id=item['flywire_id'],before_hz=prior[item['flywire_id']]['hz'],
            after_hz=item['hz'],delta_hz=item['hz']-prior[item['flywire_id']]['hz']) for item in after['mbon']]
    return dict(compatible=not reasons,reasons=reasons,warnings=warnings,delta=delta)


class MBObserver:
    def __init__(self,run_id,capacity=CAPACITY):
        if type(capacity) is not int or not 1<=capacity<=CAPACITY:raise ValueError('观察器容量无效')
        self.run_id=str(run_id);self.capacity=capacity
        self._points=deque(maxlen=capacity);self._seq=0;self._last_end=None
        self._slots=dict(before=None,after=None);self._closed=False;self._status='running'
        self._minimum_capture_seq=1
        self._lock=threading.Lock()

    def append(self,chunk,context=None):
        """Exactly once per successful neural chunk, independent of browser polling."""
        end=chunk.get('brain_time_ms');duration=chunk.get('chunk_ms')
        if not _number(end) or not _number(duration) or not 0<duration<=1000 or end<duration:
            raise ValueError('观察器需要成功脑块的实际起止时间')
        end/=1000;duration/=1000;start=end-duration
        actual=chunk.get('mb_learning') or {};obs=actual.get('observation')
        available=isinstance(obs,dict) and obs.get('schema_version')==1
        if available:
            available=(_number(obs.get('chunk_s')) and abs(obs['chunk_s']-duration)<1e-8
                and _number(obs.get('end_ms')) and abs(obs['end_ms']/1000-end)<1e-8)
        with self._lock:
            if self._closed:raise ValueError('本轮观察器已封存')
            if self._last_end is not None and end<=self._last_end+1e-9:
                raise ValueError('观察块重复或模型时间倒退')
            self._seq+=1
            point=dict(seq=self._seq,start_s=start,end_s=end,chunk_s=duration,available=available)
            if available:
                point.update(deepcopy({key:obs[key] for key in ('controls','selection_revision','pattern_revision',
                    'cues','teaching_signal','kc','eligibility','factor_before','factor_after','factor_histogram',
                    'settings_revision','reset_revision')}))
                point['mbon']=deepcopy(actual['mbon_hz'])
                point['context']=deepcopy(context or {})
            else:point['gap_reason']='局部模块尚未初始化，或此块缺少匹配的实际观测；不补造零活动。'
            self._points.append(point);self._last_end=end
            return deepcopy(point)

    def _comparisons(self):
        return dict(**deepcopy(self._slots),comparison=compare_windows(self._slots['before'],self._slots['after']))

    def read(self,after_seq=0,limit=600,status=None):
        if type(after_seq) is not int or after_seq<0 or type(limit) is not int or not 1<=limit<=600:
            raise ValueError('观察器游标或读取条数无效')
        with self._lock:
            first=self._points[0]['seq'] if self._points else self._seq+1
            candidates=[p for p in self._points if p['seq']>after_seq];page=candidates[:limit]
            return dict(schema_version=1,run_id=self.run_id,status=status or self._status,capacity=self.capacity,
                points=deepcopy(page),first_seq=first,last_seq=self._seq,dropped_before_seq=first-1,
                truncated_before=after_seq<first-1,has_more=len(candidates)>len(page),
                comparisons=self._comparisons(),sealed=self._closed,minimum_capture_seq=self._minimum_capture_seq)

    def require_fresh(self):
        """Paused edits cannot re-label pre-edit samples as a post-edit window."""
        with self._lock:self._minimum_capture_seq=self._seq+1

    def capture(self,slot,duration_s):
        if slot not in ('before','after'):raise ValueError('请选择before或after观察窗')
        if not _number(duration_s) or not .2<=duration_s<=5:raise ValueError('观察窗长度须为0.2–5模型秒')
        with self._lock:
            if self._closed:raise ValueError('本轮已封存，不能修改已保存观察窗')
            if not self._points:raise ValueError('尚无实际脑积分块可捕获')
            selected=[];duration=0.;expected=None;signature=None
            for point in reversed(self._points):
                if point['seq']<self._minimum_capture_seq:
                    raise ValueError('设置已改变；请恢复积分并等待足够的变更后模型时间，不能捕获旧读出')
                if not point['available']:raise ValueError('观察窗缺少实际局部遥测；请启用模块并等待足够模型时间')
                if expected is not None and abs(point['end_s']-expected)>1e-7:
                    raise ValueError('观察窗含缺失的模型时间块，不能捕获为完整窗口')
                current=_signature(point)
                if signature is not None and current!=signature:
                    raise ValueError('观察窗内目标或控制发生变化；请等待一段设置稳定的模型时间')
                signature=current;selected.append(point);duration+=point['chunk_s'];expected=point['start_s']
                if duration+1e-8>=duration_s:break
            if duration+1e-8<duration_s:raise ValueError('保留的完整模型时间不足，尚不能捕获该长度')
            selected.reverse();last=selected[-1]
            mbon=[]
            for item in last['mbon']:
                identity=item['flywire_id'];spikes=sum(next(v['spikes'] for v in point['mbon'] if v['flywire_id']==identity) for point in selected)
                mbon.append(dict(flywire_id=identity,spikes=spikes,hz=spikes/duration))
            kc_count=last['kc']['count'];kc_spikes=sum(point['kc']['spikes'] for point in selected)
            mean=dict(cues={cue:_weighted(selected,lambda p:p['cues'][cue],duration) for cue in ('sugar','fruit')},
                teaching_signal=_weighted(selected,lambda p:p['teaching_signal'],duration),kc_hz=kc_spikes/(duration*kc_count),
                mbon_hz=sum(v['spikes'] for v in mbon)/(duration*len(mbon)),
                eligibility_mean=_weighted(selected,lambda p:p['eligibility']['mean'],duration),
                factor_mean=_weighted(selected,lambda p:p['factor_after']['mean'],duration))
            inputs={key:_weighted(selected,lambda p:p['context'].get(key),duration) for key in ('sugar_rate_hz',)}
            p9=[]
            for side in range(2):p9.append(_weighted(selected,lambda p:(p['context'].get('p9_rate_hz') or [None,None])[side],duration))
            inputs['p9_rate_hz']=p9 if None not in p9 else None
            window=dict(schema_version=1,slot=slot,start_s=selected[0]['start_s'],end_s=last['end_s'],
                requested_duration_s=float(duration_s),duration_s=duration,samples=len(selected),
                first_seq=selected[0]['seq'],last_seq=last['seq'],mean=mean,mbon=mbon,input_mean=inputs,
                signature=signature,comparison_signature=_signature(last,comparison=True))
            self._slots[slot]=window
            return dict(window=deepcopy(window),comparisons=self._comparisons())

    def seal(self,status):
        with self._lock:self._closed=True;self._status=str(status)

    def export(self):
        with self._lock:
            first=self._points[0]['seq'] if self._points else self._seq+1
            return dict(schema_version=1,kind='mb-learning-observer',run_id=self.run_id,status=self._status,
                sealed=self._closed,capacity=self.capacity,total_chunks=self._seq,dropped_chunks=first-1,
                points=deepcopy(list(self._points)),comparisons=self._comparisons(),
                limitations='最近6000个真实脑块的有界窗口；完整新增块遥测另在mb_learning.jsonl。捕获不改变模型；条件比较不证明因果。')

    def save(self,path):
        # Compact encoding keeps repeated chunk metadata out of pretty-print overhead.
        # The read-only viewer accepts at most 32 MiB; preserve the newest whole
        # chunks if a very large selected MBON population reaches that boundary.
        value=self.export();budget=30*1024*1024
        while True:
            raw=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
            if len(raw)<=budget:break
            points=value['points']
            if not points:raise ValueError('观察器摘要超过保存大小上限')
            count=max(1,math.ceil(len(points)*(1-budget/len(raw))))
            value['points']=points[count:];value['dropped_chunks']+=count;value['truncated_by_bytes']=True
        path=Path(path);partial=path.with_name(path.name+'.partial')
        with partial.open('wb') as stream:stream.write(raw);stream.flush()
        partial.replace(path)
        return dict(bytes=len(raw),retained_chunks=len(value['points']),dropped_chunks=value['dropped_chunks'])
