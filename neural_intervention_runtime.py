"""Transient CUDA intervention execution; original sparse weights stay intact.

Window times are absolute brain integration times. Silencing removes selected
outputs before sparse propagation, clamps their local state before/after the
step, and preserves already queued currents to other neurons. Cancellation
stops the intervention; it does not rewind any earlier neural activity.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING

import torch
from neural_intervention import validate_protocol, resolve_targets, SUMMARY_LIMIT


class NeuralInterventionRuntime:
    def __init__(self,rows,dt_ms,device):
        self.rows=rows;self.dt_ms=float(dt_ms);self.device=device
        self.protocol=None;self._request=None;self.indices=[];self.gpu_indices=None
        self.start_tick=self.end_tick=0;self.active_steps_total=0
        self.first_applied_tick=self.last_applied_tick=None
        self._stim_rates=None;self._summary=[]

    def update(self,value):
        # The transport repeats its full current setting per chunk. Resolve the
        # 138k-row annotation table only when the requested protocol changes.
        if value==self._request:return False
        protocol=validate_protocol(value)
        indices=resolve_targets(protocol,self.rows)
        if protocol is None:
            start_tick=end_tick=0;gpu_indices=None
        else:
            # Each step is assigned its input model timestamp, matching worker
            # activity events; [start,end) is quantized to included step times.
            # Decimal avoids including an extra endpoint step from binary
            # arithmetic such as (0.0001 + 0.0002) / 0.0001.
            dt_s=Decimal(str(self.dt_ms))/1000
            start_s=Decimal(str(protocol['start_s']))
            end_s=start_s+Decimal(str(protocol['duration_s']))
            start_tick=int((start_s/dt_s).to_integral_value(rounding=ROUND_CEILING))
            end_tick=int((end_s/dt_s).to_integral_value(rounding=ROUND_CEILING))
            gpu_indices=torch.tensor(indices,dtype=torch.long,device=self.device)
        self._request=deepcopy(value);self.protocol=protocol;self.indices=indices
        self.gpu_indices=gpu_indices;self.start_tick=start_tick;self.end_tick=end_tick
        self.active_steps_total=0;self._stim_rates=None
        self.first_applied_tick=self.last_applied_tick=None
        self._summary=[dict(index=i,flywire_id=self.rows[i]['flywire_id'],cell_type=self.rows[i].get('cell_type'))
                       for i in indices[:SUMMARY_LIMIT]]
        return True

    def begin_chunk(self):self._stim_rates=None

    def active(self,tick):
        return self.protocol is not None and self.start_tick<=tick<self.end_tick

    def _clamp(self,state,model):
        conductance,delay_buffer,spikes,voltage,refrac=state
        target=self.gpu_indices
        conductance[:,target]=0
        delay_buffer[:,:,target]=0
        spikes[:,target]=0
        voltage[:,target]=model.neurons.neuron.v_reset
        return conductance,delay_buffer,spikes,voltage,refrac

    def step(self,model,rates,state,tick):
        """Called only for an active step; no extra random draw is performed."""
        self.active_steps_total+=1
        if self.first_applied_tick is None:self.first_applied_tick=tick
        self.last_applied_tick=tick
        if self.protocol['mode']=='silence':
            state=self._clamp(state,model)
            return self._clamp(model(rates,*state),model)
        if self._stim_rates is None:self._stim_rates=rates.clone()
        else:self._stim_rates.copy_(rates)
        target=self.gpu_indices
        # Upstream Poisson voltage is not masked to exc_indices: these indices
        # only remove the recurrent-input refractory gate for P9/sugar inputs.
        # Keep ordinary targets' existing refractory duration for the added
        # external drive. Do not alter upstream recurrent dynamics or counters.
        spikes=state[2][:,target];refrac=state[4][:,target]
        next_refrac=torch.where(spikes>0,torch.zeros_like(refrac),refrac+1)
        eligible=(next_refrac>=model.neurons.refrac_steps[target].unsqueeze(0)).to(rates.dtype)
        self._stim_rates[:,target]=torch.clamp(rates[:,target]+self.protocol['rate_hz']*eligible,max=1000)
        return model(self._stim_rates,*state)

    def summary(self,counts,chunk_ms,next_tick,active_steps):
        count=len(self.indices)
        spikes=int(counts[self.gpu_indices].sum(dtype=torch.float64).item()) if count else 0
        hz=spikes/(chunk_ms/1000) if chunk_ms else 0.
        phase=('idle' if self.protocol is None else 'scheduled' if next_tick<self.start_tick
               else 'completed' if next_tick>=self.end_tick else 'active')
        return dict(protocol=deepcopy(self.protocol),target_count=count,
                    target_ids=[row['flywire_id'] for row in self._summary],targets=deepcopy(self._summary),
                    targets_truncated=count>SUMMARY_LIMIT,phase=phase,
                    effective_start_ms=self.start_tick*self.dt_ms if count else None,
                    effective_end_ms=self.end_tick*self.dt_ms if count else None,
                    first_applied_ms=self.first_applied_tick*self.dt_ms if self.first_applied_tick is not None else None,
                    last_applied_ms=self.last_applied_tick*self.dt_ms if self.last_applied_tick is not None else None,
                    actual_end_exclusive_ms=(self.last_applied_tick+1)*self.dt_ms if self.last_applied_tick is not None else None,
                    active_steps=active_steps,active_steps_total=self.active_steps_total,
                    target_chunk_spikes=spikes,target_hz=hz,target_mean_hz=hz/count if count else 0.,
                    rate_denominator_ms=chunk_ms,
                    interpretation='Engineering external Poisson input / LIF clamp; not optogenetic kinetics; cancellation is not rollback')
