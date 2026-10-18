"""Optional local LTD on existing positive KC→MBON CSR entries.

The original matrix is never mutated. Roles use cell_class, independently of
top_nt predictions. An external teaching scalar gates local depression; DAN
counts are descriptive, not simulated dopamine release or a recovered pathway.
"""
from copy import deepcopy
import math

import torch
from mb_learning import (MBLearningControls, validate_controls, resolve_roles,
                         cue_patterns, validate_cues, validate_teaching,
                         validate_memory, MAX_MEMORY_EDGES)


class MBLearningRuntime:
    def __init__(self,base_weights,rows,controls=None):
        if base_weights.layout!=torch.sparse_csr or tuple(base_weights.shape)!=(len(rows),len(rows)):
            raise ValueError('局部学习需要与注释模型顺序一致的方形 CSR 矩阵')
        self.base_weights=base_weights;self.rows=rows;self.device=base_weights.device
        self.controls=MBLearningControls().snapshot() if controls is None else validate_controls(controls)
        self.roles=resolve_roles(self.controls,rows)
        self.kc_indices=self.roles['kc_indices'];self.mbon_indices=self.roles['mbon_indices']
        self.kc_gpu=torch.tensor(self.kc_indices,dtype=torch.long,device=self.device)
        self.mbon_gpu=torch.tensor(self.mbon_indices,dtype=torch.long,device=self.device)
        local_kc={index:position for position,index in enumerate(self.kc_indices)}
        self.edges=[]
        crow=base_weights.crow_indices()
        # Copy only endpoints + short slices for selected MBON rows, never a
        # 15-million-edge postsynaptic-index vector or the full weights table.
        for post in self.mbon_indices:
            first,last=crow[post:post+2].cpu().tolist()
            columns=base_weights.col_indices()[first:last].cpu().tolist()
            values=base_weights.values()[first:last].cpu().tolist()
            for offset,(pre,value) in enumerate(zip(columns,values)):
                if pre in local_kc and value>0:
                    self.edges.append(dict(pre_id=rows[pre]['flywire_id'],post_id=rows[post]['flywire_id'],
                        pre_index=pre,post_index=post,pre_local=local_kc[pre],position=first+offset))
                    if len(self.edges)>MAX_MEMORY_EDGES:raise ValueError('当前选择超过 30000 条可塑边，请缩小类型范围')
        self.edges.sort(key=lambda edge:(edge['post_index'],edge['pre_index']))
        if not self.edges:raise ValueError('所选类型没有既有正权重 KC→MBON 边，不能建立可塑性实验')
        if len({(edge['pre_id'],edge['post_id']) for edge in self.edges})!=len(self.edges):
            raise ValueError('所选 CSR 含重复 KC→MBON 元素，不能按精确边标识保存记忆')
        self.edge_positions=torch.tensor([e['position'] for e in self.edges],dtype=torch.long,device=self.device)
        self.edge_pre_local=torch.tensor([e['pre_local'] for e in self.edges],dtype=torch.long,device=self.device)
        self.factors=torch.ones(len(self.edges),dtype=base_weights.dtype,device=self.device)
        self.eligibility=torch.zeros(len(self.kc_indices),dtype=base_weights.dtype,device=self.device)
        self.patterns=cue_patterns(self.controls,rows,self.roles)
        self._pattern_masks=self._make_pattern_masks(self.patterns)
        self._observation_groups=self._make_observation_groups(self.patterns)
        self._observation=None
        self._factor_version=0;self._modified=False
        self._cached_nt=None;self._cached_nt_version=None;self._cached_factor_version=-1;self._composed=None
        self._input_buffer=None;self._cue_addition=None;self._cues=dict(sugar=0.,fruit=0.)
        self.trained_seconds=0.;self.paired_seconds=0.;self.enabled_seconds=0.;self.last_reward=0.
        self._mbon_spikes=[0.]*len(self.mbon_indices);self._readout_chunk_s=None
        self.selection_revision=0;self.pattern_revision=0;self.memory_origin='fresh'

    def _make_pattern_masks(self,patterns):
        local={index:position for position,index in enumerate(self.kc_indices)}
        masks={}
        for cue in ('sugar','fruit'):
            mask=torch.zeros(len(self.kc_indices),device=self.device,dtype=self.base_weights.dtype)
            positions=[local[index] for index in patterns[cue]]
            mask[torch.tensor(positions,dtype=torch.long,device=self.device)]=1
            masks[cue]=mask
        return masks

    def _make_observation_groups(self,patterns):
        local={index:position for position,index in enumerate(self.kc_indices)}
        sugar=set(patterns['sugar']);fruit=set(patterns['fruit'])
        return dict(sugar=[local[index] for index in patterns['sugar']],
                    fruit=[local[index] for index in patterns['fruit']],
                    overlap=[position for position,index in enumerate(self.kc_indices) if index in sugar and index in fruit])

    @staticmethod
    def _factor_statistics(values):
        return dict(min=min(values),mean=math.fsum(values)/len(values),
                    changed_edges=sum(value<1. for value in values))

    def _capture_observation(self,counts,chunk_s,gate,factor_before):
        # These vectors are short, temporary CPU reductions of selected cells /
        # local edges. Only the scalar summaries below are retained or sent.
        kc_counts=counts[self.kc_gpu].detach().cpu().tolist()
        traces=self.eligibility.detach().cpu().tolist()
        factors=self.factors.detach().cpu().tolist()
        groups={}
        for name,indices in self._observation_groups.items():
            number=len(indices);spikes=math.fsum(kc_counts[index] for index in indices)
            groups[name]=dict(count=number,spikes=int(spikes),
                active=sum(kc_counts[index]>0 for index in indices),
                mean_hz=spikes/(number*chunk_s) if number else None,
                eligibility_mean=math.fsum(traces[index] for index in indices)/number if number else None,
                eligibility_max=max((traces[index] for index in indices),default=None))
        histogram=[0]*10
        for factor in factors:
            # [lower,upper) bins, except [0.9,1.0] at the upper endpoint.
            histogram[min(9,max(0,int(factor*10)))]+=1
        spikes=math.fsum(kc_counts);number=len(kc_counts)
        self._observation=dict(schema_version=1,chunk_s=float(chunk_s),controls=dict(self.controls),
            selection_revision=self.selection_revision,pattern_revision=self.pattern_revision,
            cues=dict(self._cues),teaching_signal=float(gate),
            eligibility_evolved=bool(self.controls['enabled']),
            factor_updates_enabled=bool(self.controls['enabled'] and self.controls['plasticity_enabled']),
            kc=dict(count=number,spikes=int(spikes),active=sum(value>0 for value in kc_counts),
                    mean_hz=spikes/(number*chunk_s),groups=groups),
            eligibility=dict(mean=math.fsum(traces)/len(traces),max=max(traces)),
            factor_before=factor_before,factor_after=self._factor_statistics(factors),
            factor_histogram=dict(edges=[index/10 for index in range(11)],counts=histogram),
            interpretation='Actual post-intervention chunk counts, including silent selected cells in rate denominators. Sugar/fruit groups may overlap. Disabled eligibility is held unchanged; frozen factors are held unchanged. factor_after applies to the next chunk.')

    def configure(self,changes):
        """Validate/allocate all candidates before replacing any current state."""
        candidate=validate_controls(changes,self.controls)
        if candidate==self.controls:return False
        selection_changed=any(candidate[key]!=self.controls[key] for key in ('kc_type','mbon_type'))
        if selection_changed:
            replacement=type(self)(self.base_weights,self.rows,candidate)
            replacement.selection_revision=self.selection_revision+1
            replacement.pattern_revision=self.pattern_revision+1
            replacement.memory_origin='selection_changed_reset'
            self.__dict__.update(replacement.__dict__)
            return True
        if candidate['min_factor']>self.controls['min_factor'] and bool((self.factors<candidate['min_factor']).any().item()):
            raise ValueError('现有记忆低于新的最小因子；请先重置局部记忆，避免隐式增强突触')
        pattern_changed=any(candidate[key]!=self.controls[key] for key in ('coding_fraction','cue_seed'))
        if pattern_changed:
            patterns=cue_patterns(candidate,self.rows,self.roles)
            masks=self._make_pattern_masks(patterns)
            observation_groups=self._make_observation_groups(patterns)
        self.controls=candidate
        if pattern_changed:
            self.patterns=patterns;self._pattern_masks=masks
            self.eligibility.zero_();self.pattern_revision+=1
            self._observation_groups=observation_groups;self._observation=None
        self._input_buffer=None;self._cue_addition=None
        return True

    def begin_chunk(self,cues=None):
        normalized=validate_cues(cues)
        self._cues=normalized;self._input_buffer=None;self._cue_addition=None
        if not self.controls['enabled'] or not self.controls['cue_input_enabled'] or self.controls['kc_input_hz']==0:
            return
        if normalized['sugar']==0 and normalized['fruit']==0:return
        activation=torch.clamp(self._pattern_masks['sugar']*normalized['sugar']+
                               self._pattern_masks['fruit']*normalized['fruit'],max=1.)
        self._cue_addition=self.controls['kc_input_hz']*activation

    def input_rates(self,base_rates,state,model):
        if not self.controls['enabled'] or self._cue_addition is None:return base_rates
        if self._input_buffer is None:self._input_buffer=base_rates.clone()
        spikes=state[2][:,self.kc_gpu];refrac=state[4][:,self.kc_gpu]
        next_refrac=torch.where(spikes>0,torch.zeros_like(refrac),refrac+1)
        eligible=(next_refrac>=model.neurons.refrac_steps[self.kc_gpu].unsqueeze(0)).to(base_rates.dtype)
        self._input_buffer[:,self.kc_gpu]=torch.clamp(base_rates[:,self.kc_gpu]+self._cue_addition.unsqueeze(0)*eligible,max=1000)
        return self._input_buffer

    def compose(self,nt_weights):
        if not self.controls['enabled'] or not self._modified:return nt_weights
        nt_version=nt_weights._version
        if self._cached_nt is nt_weights and self._cached_nt_version==nt_version and self._cached_factor_version==self._factor_version:
            return self._composed
        if nt_weights.layout!=torch.sparse_csr or tuple(nt_weights.shape)!=tuple(self.base_weights.shape):
            raise ValueError('NT 调制矩阵必须保留原 CSR 模型形状与索引')
        same_crow=(nt_weights.crow_indices().data_ptr()==self.base_weights.crow_indices().data_ptr()
                   or torch.equal(nt_weights.crow_indices(),self.base_weights.crow_indices()))
        same_columns=(nt_weights.col_indices().data_ptr()==self.base_weights.col_indices().data_ptr()
                      or torch.equal(nt_weights.col_indices(),self.base_weights.col_indices()))
        if not same_crow or not same_columns:
            raise ValueError('NT 调制不能改变局部学习的原 CSR 元素索引')
        values=nt_weights.values().clone()
        values[self.edge_positions]=values[self.edge_positions]*self.factors
        composed=torch.sparse_csr_tensor(nt_weights.crow_indices(),nt_weights.col_indices(),values,
            size=nt_weights.shape,device=nt_weights.device,dtype=nt_weights.dtype)
        self._cached_nt=nt_weights;self._cached_nt_version=nt_version
        self._cached_factor_version=self._factor_version;self._composed=composed
        return composed

    def observe(self,counts,chunk_s,teaching_signal=0.):
        if type(chunk_s) not in (int,float) or not 0<chunk_s<=1 or not math.isfinite(chunk_s):
            raise ValueError('资格迹需要实际 0–1 模型秒的非零 chunk 时间')
        gate=validate_teaching(teaching_signal)
        if counts.ndim!=1 or counts.shape[0]!=len(self.rows):raise ValueError('局部学习读出须来自本块完整模型实际计数')
        factor_before=self._factor_statistics(self.factors.detach().cpu().tolist())
        self._mbon_spikes=counts[self.mbon_gpu].detach().cpu().tolist();self._readout_chunk_s=float(chunk_s)
        if not self.controls['enabled']:
            self._capture_observation(counts,chunk_s,gate,factor_before)
            return
        self.enabled_seconds+=chunk_s;self.last_reward=gate
        # Aggregate spike-triggered eligibility: each actual KC spike adds .05,
        # then bounded at 1; previous eligibility decays by actual model dt.
        # Depositing chunk counts at its end is an explicit approximation.
        decay=math.exp(-chunk_s/self.controls['eligibility_tau_s'])
        self.eligibility.mul_(decay).add_(counts[self.kc_gpu]*.05).clamp_(0,1)
        if not self.controls['plasticity_enabled']:
            self._capture_observation(counts,chunk_s,gate,factor_before)
            return
        self.trained_seconds+=chunk_s
        if gate<=0 or self.controls['learning_rate']==0:
            self._capture_observation(counts,chunk_s,gate,factor_before)
            return
        self.paired_seconds+=chunk_s
        updated=torch.clamp(self.factors-self.controls['learning_rate']*gate*self.eligibility[self.edge_pre_local]*chunk_s,
                            min=self.controls['min_factor'],max=1.)
        if not torch.equal(updated,self.factors):
            self.factors=updated;self._factor_version+=1
            self._modified=bool((self.factors<1).any().item())
        self._capture_observation(counts,chunk_s,gate,factor_before)

    def reset(self):
        self.factors.fill_(1);self.eligibility.zero_();self._factor_version+=1;self._modified=False
        self.trained_seconds=0.;self.paired_seconds=0.;self.enabled_seconds=0.;self.last_reward=0.
        self._input_buffer=None;self._cue_addition=None;self.memory_origin='reset'
        self._observation=None

    def export_memory(self):
        factors=self.factors.detach().cpu().tolist()
        # Device float32 can represent a configured .9 floor as .899999976.
        # Normalize only the JSON boundary so our own saved memory is valid;
        # actual live factors/state and trajectory are never changed here.
        normalized=[min(1.,max(self.controls['min_factor'],float(factor))) for factor in factors]
        return dict(schema_version=1,selection={key:self.controls[key] for key in ('kc_type','mbon_type')},
                    edges=[dict(pre_id=edge['pre_id'],post_id=edge['post_id'],factor=factor) for edge,factor in zip(self.edges,normalized)])

    def load_memory(self,value):
        normalized=validate_memory(value,self.controls,self.edges)
        factors=torch.tensor([edge['factor'] for edge in normalized['edges']],dtype=self.base_weights.dtype,device=self.device)
        modified=bool((factors<1).any().item())
        self.factors=factors;self.eligibility.zero_();self._factor_version+=1;self._modified=modified
        self.trained_seconds=0.;self.paired_seconds=0.;self.enabled_seconds=0.;self.last_reward=0.
        self._input_buffer=None;self._cue_addition=None;self.memory_origin='loaded_without_traces'
        self._observation=None

    def summary(self,counts=None,chunk_s=None):
        if counts is not None:
            if chunk_s is None or not 0<chunk_s<=1:raise ValueError('读出须提供实际 chunk 模型秒')
            spikes=counts[self.mbon_gpu].detach().cpu().tolist();duration=chunk_s
        else:spikes=self._mbon_spikes;duration=self._readout_chunk_s
        mbon_hz=[dict(index=index,flywire_id=self.rows[index]['flywire_id'],spikes=int(number),
                     hz=number/duration if duration else None)
                 for index,number in zip(self.mbon_indices,spikes)]
        changed=int((self.factors<1).sum().item())
        return dict(controls=dict(self.controls),kc_count=len(self.kc_indices),mbon_count=len(self.mbon_indices),
            role_counts=dict(self.roles['class_counts']),role_source=self.roles['role_source'],
            edge_count=len(self.edges),changed_edges=changed,min_factor=float(self.factors.min().item()),
            mean_factor=float(self.factors.double().mean().item()),trained_seconds=self.trained_seconds,
            enabled_seconds=self.enabled_seconds,reward_paired_seconds=self.paired_seconds,last_reward=self.last_reward,
            mbon_hz=mbon_hz,mbon_mean_hz=sum(spikes)/(duration*len(spikes)) if duration and spikes else None,
            rate_denominator_s=duration,patterns=deepcopy(self.patterns['statistics']),last_cues=dict(self._cues),
            selection_revision=self.selection_revision,pattern_revision=self.pattern_revision,memory_origin=self.memory_origin,
            effective_local_factors_enabled=self.controls['enabled'],eligibility_restored=False,
            observation=deepcopy(self._observation),
            formula='e_new=clip(exp(-dt/tau)*e_old+0.05*KC_spike_count,0,1); factor_new=clip(factor-lr*teaching*e_new*dt,min_factor,1); W_eff=W_NT*factor on selected existing positive KC→MBON entries only',
            timing='Observe actual chunk counts; changed factors affect the next chunk. Delayed currents already elsewhere are not rewound.',
            limitations='External engineering teaching gate, not reconstructed DAN release; deterministic odor→KC bridge, not ORN→PN; no behavior decoder from MBON; local LTD only, no new edges/sign changes; aggregate spike eligibility, not precise biological synaptic timing')

    def metadata(self):return self.summary()
