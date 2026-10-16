"""Measure physical turning response; no brain or navigation policy involved."""
import json
import math
from habitat_world import HabitatConfig, build_habitat, HabitatSensors
from play_fly import HybridControllerObservation, apply_locomotion_action


def measure(drive, seconds=1.2):
    config = HabitatConfig(spawn=[0, 0], obstacles=[])
    fly, sim, _, controller = build_habitat(config, seed=1)
    sensors = HabitatSensors(config, fly, sim)
    start = sensors.read()
    previous = start['yaw_rad']
    yaw = 0.0
    minimum = 1.0
    for tick in range(round(seconds / sim.timestep)):
        if tick % 5 == 0:
            observation = HybridControllerObservation.from_sim(sim, fly.name)
            apply_locomotion_action(sim, fly.name, controller.step(drive, observation))
        sim.step()
        if tick % 200 == 199:
            sense = sensors.read()
            current = sense['yaw_rad']
            yaw += math.atan2(math.sin(current-previous), math.cos(current-previous))
            previous = current
            minimum = min(minimum, sense['upright'])
    finish = sensors.read()
    return {'drive': drive, 'seconds': seconds, 'yaw_change_rad': yaw,
            'displacement_mm': math.dist(start['position_mm'][:2], finish['position_mm'][:2]),
            'minimum_upright': minimum}


if __name__ == '__main__':
    results = []
    for drive in ([.08,1.2], [.35,1.05], [.5,1.05], [1.05,.35], [-.6,.6], [.6,-.6], [-.6,-.6]):
        result = measure(drive)
        results.append(result)
        print(json.dumps(result), flush=True)
    from pathlib import Path
    Path('output/habitat/turn_calibration.json').write_text(json.dumps(results, indent=2))
