"""Unit checks for live engineering gains; no CUDA or running experiment needed."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

from brain_controls import BrainControls, schema


def main():
    defaults = BrainControls()
    command = dict(state='example', p9_rate_hz=[85.0, 35.0], sugar_rate_hz=200.0,
                   desired_drive=[.85, -.35], motor_sign=[1, -1])
    encoded = defaults.encode(command)
    assert encoded['p9_rate_hz'] == command['p9_rate_hz']
    assert encoded['sugar_rate_hz'] == command['sugar_rate_hz']
    assert defaults.motor_drive([.85, .35], [1, -1]) == [.85, -.35]
    adjusted = defaults.updated(dict(p9_gain_a=0, p9_gain_b=2, p9_bias_hz=10,
                                     sugar_gain=3, motor_gain=1.5, smoothing_ms=40))
    assert adjusted.encode(command)['p9_rate_hz'] == [10.0, 80.0]
    assert adjusted.encode(command)['sugar_rate_hz'] == 600.0
    assert adjusted.motor_drive([1.1, .2], [1, -1]) == [1.2, -.30000000000000004]
    assert adjusted.updated({'feedback_enabled': False}).motor_drive([1, 1], [1, -1]) == [0, 0]
    assert adjusted.encode({**command, 'sugar_rate_hz': 0})['sugar_rate_hz'] == 0
    assert defaults.snapshot() == schema()['defaults']
    assert all(value==1 for value in defaults.transmitter_gains().values())
    assert defaults.updated({'nt_gaba':.5}).transmitter_gains()['gaba']==.5
    try:
        defaults.p9_gain_a = 2
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError('Control snapshots must be immutable')
    assert command['p9_rate_hz'] == [85, 35] and defaults.p9_gain_a == 1
    invalid = [{}, {'unknown':1}, {'p9_gain_a':-1}, {'p9_gain_b':float('nan')},
               {'p9_bias_hz':float('inf')}, {'sugar_gain':True}, {'motor_gain':2},
               {'smoothing_ms':0}, {'feedback_enabled':1}, {'p9_gain_a':1,'sugar_gain':9},
               {'nt_gaba':-1},{'nt_dopamine':True},{'nt_acetylcholine':float('nan')}]
    for changes in invalid:
        try:
            defaults.updated(changes)
        except ValueError:
            pass
        else:
            raise AssertionError(changes)
        assert defaults.snapshot() == schema()['defaults']
    result=dict(status='passed', default_bridge_unchanged=True, stimulus_mapping=True,
                motor_clamp=True, feedback_disconnect=True, validation_atomic=True,
                invalid_requests=len(invalid))
    folder=Path(__file__).resolve().parent/'output/brain_controls'
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'unit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
