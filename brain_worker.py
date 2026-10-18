"""Persistent upstream FlyWire LIF model on CUDA, with a JSON-lines interface.

P9 IDs and LIF equations come from eonsystemspbc/fly-brain. Mapping their
measured firing rates to CPG amplitudes is an engineering demonstration.
"""
import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
sys.path.insert(0, str(ROOT / "brain-model" / "code"))

# Upstream requires Arrow before torch on Windows.
import pyarrow  # noqa: F401
import numpy as np
import torch
from benchmark import path_comp, path_con, get_experiment
from run_pytorch import DT, MODEL_PARAMS, TorchModel, get_hash_tables, get_weights
from neuron_annotations import annotations
from transmitter_modulation import TransmitterModulation, validate_gains
from project_info import project_info
from neural_intervention import UNSET
from neural_intervention_runtime import NeuralInterventionRuntime
from mb_learning import MBLearningControls, validate_controls, validate_cues, validate_teaching
from mb_learning_runtime import MBLearningRuntime


class Brain:
    def __init__(self, add_sugar_inputs=False, seed=42):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; this launcher requires the NVIDIA GPU")
        torch.manual_seed(seed)
        self.ids, self.inverse_ids = get_hash_tables(path_comp)
        self.p9_ids = get_experiment("p9")["neu_exc"]
        self.p9_indices = [self.ids[i] for i in self.p9_ids]
        self.sugar_ids = get_experiment("sugar")["neu_exc"] if add_sugar_inputs else []
        self.sugar_indices = [self.ids[i] for i in self.sugar_ids]
        with contextlib.redirect_stdout(sys.stderr):
            weights = get_weights(path_con, path_comp, ROOT / "brain-model" / "data")
        weights = weights.to("cuda")
        self.model = TorchModel(
            1, weights.shape[0], DT, MODEL_PARAMS, weights,
            exc_indices=self.p9_indices + self.sugar_indices, device="cuda",
        )
        self.state = self.model.state_init()
        self.rates = torch.zeros(1, weights.shape[0], device="cuda")
        self.total_counts = torch.zeros(weights.shape[0], device="cuda")
        self.sim_ms = 0.0
        self.wall_seconds = 0.0
        self.size = weights.shape[0]
        self.edges = weights._nnz()
        self.smoothed_drive = np.zeros(2)
        self.habitat_mode = add_sugar_inputs
        self.drive_smoothing_ms = 100.0 if add_sugar_inputs else 40.0
        self.annotations = annotations()
        if not self.annotations.rows:
            raise RuntimeError('Model-order annotations are required for transmitter controls')
        self.transmitters = TransmitterModulation(weights, self.annotations.rows)
        self.intervention = NeuralInterventionRuntime(self.annotations.rows,DT,self.rates.device)
        self.integration_steps = 0
        self.mb_learning=None
        self.mb_controls=MBLearningControls().snapshot()
        self.mb_settings_revision=self.mb_reset_revision=0
        self.mb_reward=0.
        self.mb_observation_clock=None

    def learning_summary(self):
        result=self.mb_learning.summary() if self.mb_learning else dict(
            controls=dict(self.mb_controls),initialized=False,edge_count=None,changed_edges=0,
            min_factor=None,mean_factor=None,trained_seconds=0.,last_reward=0.,mbon_hz=[])
        observation=result.get('observation')
        if observation is not None:
            # Revisions belong to the observed chunk, never a later paused edit.
            clock=getattr(self,'mb_observation_clock',None)
            result['observation']=dict(observation,**clock) if clock else None
        return dict(result,initialized=self.mb_learning is not None,
                    settings_revision=self.mb_settings_revision,reset_revision=self.mb_reset_revision,
                    teaching_signal_requested=self.mb_reward,brain_time_ms=self.sim_ms)

    @torch.no_grad()
    def configure_learning(self,packet=None,include_memory=False):
        if packet is not None:
            keys={'controls','settings_revision','reset_revision','initial_memory','cue','reward'}
            if not isinstance(packet,dict) or set(packet)-keys:raise ValueError('局部学习消息字段无效')
            controls=validate_controls(packet.get('controls',self.mb_controls))
            settings=packet.get('settings_revision',self.mb_settings_revision)
            reset=packet.get('reset_revision',self.mb_reset_revision)
            for value,current in ((settings,self.mb_settings_revision),(reset,self.mb_reset_revision)):
                if type(value) is not int or not current<=value<=10**9:raise ValueError('局部学习修订号无效或倒退')
            reward=validate_teaching(packet.get('reward',self.mb_reward))
            validate_cues(packet.get('cue'))
            memory=packet.get('initial_memory')
            if memory is not None and self.mb_learning is not None:
                raise ValueError('学习记忆只允许在新脑工作进程首次配置时载入')
            runtime=self.mb_learning
            if runtime is None and (controls['enabled'] or memory is not None or reset!=self.mb_reset_revision):
                runtime=MBLearningRuntime(self.transmitters.base_weights,self.annotations.rows,controls)
                if memory is not None:runtime.load_memory(memory)
            elif runtime is not None:
                runtime.configure(controls)
            if runtime is not None and reset!=self.mb_reset_revision:runtime.reset()
            self.mb_learning=runtime;self.mb_controls=controls
            self.mb_settings_revision=settings;self.mb_reset_revision=reset;self.mb_reward=reward
            self.model.weights=runtime.compose(self.transmitters.weights) if runtime else self.transmitters.weights
        result=dict(brain_time_ms=self.sim_ms,mb_learning=self.learning_summary())
        if include_memory:result['learning_memory']=self.mb_learning.export_memory() if self.mb_learning else None
        return result

    # Keep no_grad, as in the upstream runner: CSR transpose on this build
    # requires tensor version counters that inference_mode removes.
    @torch.no_grad()
    def advance(self, rate_hz, duration_ms, capture_activity=False, sugar_rate_hz=0, drive_smoothing_ms=None,
                transmitter_gains=None, intervention=UNSET,mb_learning=UNSET):
        rates = np.asarray(rate_hz, dtype=float)
        if rates.shape != (2,) or not np.isfinite(rates).all() or np.any(rates < 0) or np.any(rates > 1000):
            raise ValueError("rate_hz must be two finite numbers in [0, 1000]")
        if not np.isfinite(duration_ms) or not 0 < duration_ms <= 1000:
            raise ValueError("duration_ms must be in (0, 1000]")
        if not np.isfinite(sugar_rate_hz) or not 0 <= sugar_rate_hz <= 1000:
            raise ValueError("sugar_rate_hz must be in [0, 1000]")
        if sugar_rate_hz and not self.sugar_indices:
            raise ValueError("Sugar inputs require --habitat")
        if drive_smoothing_ms is not None:
            if type(drive_smoothing_ms) not in (int,float) or not np.isfinite(drive_smoothing_ms) or not 20 <= drive_smoothing_ms <= 500:
                raise ValueError("drive_smoothing_ms must be finite and in [20,500]")
        steps = max(1, round(duration_ms / DT))
        if capture_activity and steps > 1000:
            raise ValueError("Activity capture supports chunks up to 100 ms")
        if intervention is not UNSET:self.intervention.update(intervention)
        if mb_learning is not UNSET and mb_learning is not None:self.configure_learning(mb_learning)
        if transmitter_gains is not None:
            validate_gains(transmitter_gains, self.transmitters.gains)
            self.transmitters.apply(transmitter_gains)
        self.model.weights=self.mb_learning.compose(self.transmitters.weights) if self.mb_learning else self.transmitters.weights
        if self.mb_learning:
            self.mb_learning.begin_chunk(mb_learning.get('cue') if isinstance(mb_learning,dict) else None)
        if drive_smoothing_ms is not None:
            self.drive_smoothing_ms = float(drive_smoothing_ms)
        actual_ms = steps * DT
        start_ms = self.sim_ms
        self.rates.zero_()
        self.rates[0, self.p9_indices] = torch.tensor(rates, device="cuda", dtype=torch.float32)
        if self.sugar_indices:
            self.rates[0, self.sugar_indices] = sugar_rate_hz
        counts = torch.zeros_like(self.total_counts)
        trace = torch.empty((steps, self.size), dtype=torch.bool, device="cuda") if capture_activity else None
        torch.cuda.synchronize()
        started = time.perf_counter()
        self.intervention.begin_chunk()
        intervention_steps=0
        for step in range(steps):
            step_rates=self.mb_learning.input_rates(self.rates,self.state,self.model) if self.mb_learning else self.rates
            if self.intervention.active(self.integration_steps+step):
                self.state=self.intervention.step(self.model,step_rates,self.state,self.integration_steps+step)
                intervention_steps+=1
            else:
                self.state = self.model(step_rates, *self.state)
            counts += self.state[2][0]
            if trace is not None:
                trace[step] = self.state[2][0] > 0
        torch.cuda.synchronize()
        wall_seconds = time.perf_counter() - started
        self.total_counts += counts
        self.sim_ms += actual_ms
        self.integration_steps += steps
        if self.mb_learning:
            self.mb_learning.observe(counts,actual_ms/1000,self.mb_reward)
            self.mb_observation_clock=dict(start_ms=start_ms,end_ms=self.sim_ms,
                settings_revision=self.mb_settings_revision,reset_revision=self.mb_reset_revision)
        self.wall_seconds += wall_seconds
        p9_hz = counts[self.p9_indices].cpu().numpy() / (actual_ms / 1000)
        # Smooth raw rates before saturation in habitat mode. Clipping each
        # 20-ms count first biased the Poisson readout downwards asymmetrically.
        alpha = 1 - np.exp(-actual_ms / self.drive_smoothing_ms)
        target = p9_hz / 100 if self.habitat_mode else np.clip(p9_hz / 100, 0, 1.2)
        self.smoothed_drive += alpha * (target - self.smoothed_drive)
        result = {
            "brain_time_ms": self.sim_ms,
            "chunk_ms": actual_ms,
            "chunk_wall_seconds": wall_seconds,
            "p9_hz": p9_hz.tolist(),
            "drive": np.clip(self.smoothed_drive,0,1.2).tolist(),
            "chunk_spikes": int(counts.sum().item()),
            "total_spikes": int(self.total_counts.sum().item()),
            "active_neurons": int((self.total_counts > 0).sum().item()),
            "vram_allocated_mb": torch.cuda.max_memory_allocated() / 1024**2,
            "sugar_rate_hz": float(sugar_rate_hz),
            "sugar_spikes": int(counts[self.sugar_indices].sum().item()) if self.sugar_indices else 0,
            "drive_smoothing_ms": self.drive_smoothing_ms,
            "transmitter_gains": dict(self.transmitters.gains),
            "intervention":self.intervention.summary(counts,actual_ms,self.integration_steps,intervention_steps),
            "mb_learning":self.learning_summary(),
        }
        if trace is not None:
            spike_steps, neuron_indices = trace.nonzero(as_tuple=True)
            events_total = neuron_indices.numel()
            max_events = 20000
            spike_steps = spike_steps[:max_events].cpu().numpy()
            neuron_indices = neuron_indices[:max_events].cpu().numpy()
            active = counts.nonzero().flatten()
            active_indices = active.cpu().tolist()
            active_counts = counts[active].cpu().tolist()
            top_order = np.argsort(active_counts)[::-1][:20]
            result["activity"] = {
                "events": [[round(start_ms + int(t) * DT, 4), int(n)]
                           for t, n in zip(spike_steps, neuron_indices)],
                "events_total": events_total,
                "events_truncated": events_total > max_events,
                "active_this_chunk": len(active_indices),
                "top_neurons": [
                    {"index": active_indices[i], "flywire_id": str(self.inverse_ids[active_indices[i]]),
                     "annotation": self.annotations.at(active_indices[i]),
                     "spikes": int(active_counts[i]), "hz": active_counts[i] / (actual_ms / 1000)}
                    for i in top_order
                ],
                "p9_indices": self.p9_indices,
                "p9_voltage_mv": self.state[3][0, self.p9_indices].cpu().tolist(),
                "input_rate_hz": rates.tolist(),
            }
        return result

    def metadata(self):
        return {
            "project":project_info(),
            "device": torch.cuda.get_device_name(), "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda, "neurons": self.size,
            "weighted_edges": self.edges, "p9_ids": [str(i) for i in self.p9_ids],
            "dt_ms": DT,
            "drive_smoothing_ms": self.drive_smoothing_ms,
            "transmitter_modulation": self.transmitters.metadata(),
            "mb_learning":dict(supported=True,defaults=MBLearningControls().snapshot(),
                interpretation='Optional existing positive KC→MBON local LTD; external teaching and deterministic engineering odor→KC bridge; not full-brain or molecular learning'),
            "neural_intervention":dict(supported=True,max_targets=10000,
                input='Additional 0–1000 Hz Poisson drive, summed rate capped at 1000 Hz; existing ordinary-target refractory gate retained',
                silence='Local voltage/conductance/incoming delay/spikes clamped; already queued currents elsewhere preserved',
                timing='Absolute brain model time, included integration step timestamps in [start,end)',
                interpretation='Virtual engineering intervention; not calibrated optogenetics or biological damage'),
            "sugar_grn_ids": [str(i) for i in self.sugar_ids],
            "annotations": self.annotations.summary,
            "p9_channel_ids": [str(i) for i in self.p9_ids],
            "bridge": ("Stimulate P9; measured rates set CPG amplitudes. Engineering reflexes set direction/stop."
                       if self.habitat_mode else
                       "Stimulate P9; measured P9 firing rates modulate a hybrid CPG. Engineering mapping."),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--habitat", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    brain = Brain(add_sugar_inputs=args.habitat, seed=args.seed)
    if args.probe:
        output = {"metadata": brain.metadata(), "baseline": brain.advance([0, 0], 100),
                  "stimulated": brain.advance([100, 100], 200)}
        output["brain_sim_seconds"] = brain.sim_ms / 1000
        output["simulation_wall_seconds"] = brain.wall_seconds
        output["realtime_ratio"] = output["brain_sim_seconds"] / brain.wall_seconds
        (ROOT / "output").mkdir(exist_ok=True)
        (ROOT / "output" / "brain_report.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps(output, indent=2), flush=True)
        if output["stimulated"]["total_spikes"] <= output["baseline"]["total_spikes"]:
            raise RuntimeError("P9 stimulation generated no spikes")
        return
    print(json.dumps({"ready": brain.metadata()}), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("quit"):
            break
        if 'mb_learning_control' in message or message.get('mb_learning_export'):
            try:
                result=brain.configure_learning(message.get('mb_learning_control'),bool(message.get('mb_learning_export')))
            except (ValueError,TypeError,KeyError) as error:
                result=dict(error=str(error),brain_time_ms=brain.sim_ms)
            print(json.dumps(result),flush=True)
            continue
        options={"intervention":message['intervention']} if 'intervention' in message else {}
        if 'mb_learning' in message:options['mb_learning']=message['mb_learning']
        print(json.dumps(brain.advance(message["rate_hz"], message.get("duration_ms", 20),
                                       message.get("capture_activity", False),
                                       message.get("sugar_rate_hz", 0),
                                       message.get("drive_smoothing_ms"),
                                       message.get("transmitter_gains"),**options)), flush=True)


if __name__ == "__main__":
    main()
