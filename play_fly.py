"""Local NeuroMechFly walking demo, based on the official turning tutorial.

W: forward, A/D: turn, S: stop, R: reset. Close the viewer to exit.
The body uses the published hybrid CPG controller, not a whole-brain model.
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
os.environ.setdefault("NUMBA_CACHE_DIR", str(ROOT / ".cache" / "numba"))
os.environ.setdefault("FLYGYM_ASSET_CACHE_DIR", str(ROOT / ".cache" / "flygym_assets"))

import mujoco
import mujoco.viewer
import numpy as np
from startup_support import available_port, open_dashboard
from project_info import project_info

from flygym import Simulation
from flygym.anatomy import BodySegment, ContactBodiesPreset
from flygym.compose import FlatGroundWorld
from flygym.utils.math import Rotation3D
from flygym_demo.complex_terrain import (
    HybridControllerObservation,
    HybridTurningController,
    LocomotionAction,
    PreprogrammedSteps,
    apply_locomotion_action,
    make_locomotion_fly,
)

def make_sim():
    fly = make_locomotion_fly(name="fly", add_adhesion=True, colorize=True)
    camera = fly.add_tracking_camera(
        name="body_cam", pos_offset=(-0.5, -7.5, 0.0),
        rotation=Rotation3D("euler", (1.57, 0.0, 0.0)), fovy=35.0,
    )
    world = FlatGroundWorld()
    world.add_fly(
        fly, [0, 0, 0.8], Rotation3D("quat", [1, 0, 0, 0]),
        bodysegs_with_ground_contact=ContactBodiesPreset.TIBIA_TARSUS_ONLY,
        add_ground_contact_sensors=False,
    )
    sim = Simulation(world)
    steps = PreprogrammedSteps()
    order = fly.get_actuated_jointdofs_order("position")
    controller = HybridTurningController(
        timestep=sim.timestep, preprogrammed_steps=steps, output_dof_order=order,
    )

    def reset():
        sim.reset()
        controller.reset(seed=0)
        initial = LocomotionAction(
            joint_angles=steps.default_pose_by_dof_order(order),
            adhesion_onoff=np.ones(6, dtype=bool),
        )
        apply_locomotion_action(sim, fly.name, initial)
        sim.warmup()

    reset()
    return fly, sim, camera, controller, reset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--seconds", type=float, default=60.0,
                        help="Body model seconds, not wall seconds")
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--brain", action="store_true",
                        help="Run a live whole-brain CUDA worker; body waits for computed brain time")
    parser.add_argument("--monitor", action="store_true", help="Show a local live brain activity dashboard")
    parser.add_argument("--monitor-port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the dashboard automatically")
    parser.add_argument("--brain-drive", type=Path,
                        help="Replay measured brain-derived drive CSV (time_s,left,right)")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.brain and args.brain_drive:
        parser.error("Choose either live --brain or --brain-drive replay")
    if args.monitor and not args.brain:
        parser.error("--monitor requires --brain")
    if args.monitor:
        try:args.monitor_port=available_port(args.monitor_port if args.monitor_port is not None else 8470,automatic=args.monitor_port is None)
        except (ValueError,RuntimeError) as error:parser.error(str(error))
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    output_name = "coupled" if args.brain or args.brain_drive else "body"
    fly, sim, camera, controller, reset = make_sim()
    thorax_index = fly.get_bodysegs_order().index(BodySegment("c_thorax"))
    start_pos = sim.get_body_positions(fly.name)[thorax_index].copy()
    signal = np.array([1.0, 1.0])
    reset_requested = False
    drive = None
    brain = None
    brain_log = None
    brain_rows = []
    brain_metadata = None
    brain_state = None
    next_brain_time = 0.0
    monitor = None
    if args.monitor:
        from activity_monitor import ActivityMonitor
        monitor = ActivityMonitor(port=args.monitor_port)
        print(f"Brain activity dashboard: {monitor.url}", flush=True)
        open_dashboard(monitor.url,enabled=not args.no_browser)
    if args.brain_drive:
        drive = np.genfromtxt(args.brain_drive, delimiter=",", names=True)
        drive = np.atleast_1d(drive)
        if not {"time_s", "left", "right"}.issubset(drive.dtype.names or ()):
            parser.error("Brain drive CSV must have time_s,left,right columns")
        if not len(drive) or not np.isfinite(
            np.column_stack([drive[n] for n in ("time_s", "left", "right")])
        ).all() or np.any(np.diff(drive["time_s"]) <= 0):
            parser.error("Brain drive must contain finite values and increasing times")
        args.seconds = min(args.seconds, float(drive["time_s"][-1]))

    def key_callback(key):
        nonlocal reset_requested
        if drive is not None:
            return
        commands = {87: [1.0, 1.0], 65: [0.4, 1.2],
                    68: [1.2, 0.4], 83: [0.0, 0.0]}
        if key in commands:
            signal[:] = commands[key]
            print("Walking command:", signal, flush=True)
        elif key == 82:
            reset_requested = True

    renderer = None
    if args.video or args.headless:
        renderer = sim.set_renderer(
            [camera], camera_res=(480, 640), playback_speed=0.2,
            output_fps=25, buffer_frames=args.video,
        )
    viewer = None
    if not args.headless:
        viewer = mujoco.viewer.launch_passive(
            sim.mj_model, sim.mj_data, key_callback=key_callback,
        )
        viewer.cam.type = mujoco.mjtCamera.mjCAMERA_FIXED
        viewer.cam.fixedcamid = mujoco.mj_name2id(
            sim.mj_model, mujoco.mjtObj.mjOBJ_CAMERA, camera.name,
        )
    if args.brain:
        brain_log = (out / "brain_worker.log").open("w", encoding="utf-8")
        brain_env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1")
        brain = subprocess.Popen(
            [str(ROOT / ".brain-venv" / "Scripts" / "python.exe"), str(ROOT / "brain_worker.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=brain_log,
            text=True, encoding="utf-8", cwd=ROOT, env=brain_env,
        )
        print("Loading live FlyWire brain on CUDA. See output/brain_worker.log for startup errors.", flush=True)
        ready_line = brain.stdout.readline()
        if not ready_line:
            brain.wait()
            brain_log.close()
            if viewer:
                viewer.close()
            if renderer:
                renderer.close()
            if monitor:
                monitor.close()
            raise RuntimeError("Brain worker failed to start; see output/brain_worker.log")
        brain_metadata = json.loads(ready_line)["ready"]
        if monitor:
            monitor.ready(brain_metadata)
        print(json.dumps(brain_metadata, indent=2), flush=True)
    print("NeuroMechFly body simulation. W forward / A,D turn / S stop / R reset.", flush=True)
    print("Controller: live P9 output + hybrid CPG; engineering mapping, no sensory feedback" if brain else
          "Controller: hybrid CPG" if drive is None else
          "Controller: recorded brain output + hybrid CPG (offline replay)", flush=True)
    wall_start = time.perf_counter()
    frames = 0
    completed_steps = 0
    next_sync = 0.0
    try:
        for step in range(int(args.seconds / sim.timestep)):
            if viewer and not viewer.is_running():
                break
            if reset_requested:
                if brain:
                    print("R resets only the body; neural state is preserved.", flush=True)
                reset()
                reset_requested = False
            model_time = step * sim.timestep
            body_signal = signal
            if brain and model_time >= next_brain_time - sim.timestep / 2:
                request = {"rate_hz": (np.clip(signal, 0, 1.2) * 100).tolist(),
                           "duration_ms": min(20, (args.seconds - model_time) * 1000),
                           "capture_activity": bool(monitor)}
                brain.stdin.write(json.dumps(request) + "\n")
                brain.stdin.flush()
                response = brain.stdout.readline()
                if not response:
                    raise RuntimeError("Brain worker stopped; see output/brain_worker.log")
                brain_state = json.loads(response)
                if monitor:
                    monitor.update(brain_state)
                brain_rows.append(brain_state)
                next_brain_time += brain_state["chunk_ms"] / 1000
                print(f"Brain {brain_state['brain_time_ms']:.0f} ms; "
                      f"P9 {np.round(brain_state['p9_hz'], 1)} Hz; "
                      f"chunk {brain_state['chunk_wall_seconds']:.2f} wall seconds", flush=True)
            if brain:
                body_signal = np.asarray(brain_state["drive"])
            if drive is not None:
                signal[:] = [np.interp(model_time, drive["time_s"], drive[name])
                             for name in ("left", "right")]
            obs = HybridControllerObservation.from_sim(sim, fly.name)
            action = controller.step(body_signal, obs)
            apply_locomotion_action(sim, fly.name, action)
            sim.step()
            completed_steps = step + 1
            if not np.isfinite(sim.mj_data.qpos).all():
                raise RuntimeError("Body simulation produced non-finite joint positions")
            if renderer and sim.render_as_needed():
                frames += 1
            if viewer and model_time >= next_sync:
                viewer.sync()
                next_sync = model_time + 1 / 60
                # Keep the standalone body viewer near real time.
                remaining = model_time - (time.perf_counter() - wall_start)
                if remaining > 0 and not brain:
                    time.sleep(min(remaining, 0.02))
        end_pos = sim.get_body_positions(fly.name)[thorax_index].copy()
        report = {
            "project":project_info(),
            "controller": "live_p9_plus_cpg" if brain else "hybrid_cpg" if drive is None else "brain_output_replay_plus_cpg",
            "steps": completed_steps,
            "body_model_seconds": completed_steps * sim.timestep,
            "wall_seconds": time.perf_counter() - wall_start,
            "displacement_mm": (end_pos - start_pos).tolist(),
            "finite_state": bool(np.isfinite(sim.mj_data.qpos).all()),
            "rendered_frames": frames,
        }
        if brain:
            report["brain_metadata"] = brain_metadata
            report["brain_last_chunk"] = brain_state
            report["bridge_limitation"] = "P9 stimulation-to-CPG mapping. No simulated sensory feedback. Not validated behavior reproduction."
            (out / "coupled_brain.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in brain_rows), encoding="utf-8",
            )
        if renderer:
            import imageio.v3 as iio
            renderer.mj_renderer.update_scene(sim.mj_data, camera.name)
            iio.imwrite(out / f"{output_name}.png", renderer.mj_renderer.render())
            if args.video:
                renderer.save_video(out / f"{output_name}.mp4")
        (out / f"{output_name}_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8",
        )
        print(json.dumps(report, indent=2), flush=True)
    finally:
        if monitor:
            monitor.finish()
        if brain:
            try:
                if brain.poll() is None:
                    brain.stdin.write('{"quit": true}\n')
                    brain.stdin.flush()
                    brain.wait(timeout=10)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                brain.terminate()
                brain.wait(timeout=10)
            brain_log.close()
        if viewer:
            viewer.close()
        if renderer:
            renderer.close()
        if monitor:
            # Save the final snapshot for verification and later inspection.
            (out / "activity_snapshot.json").write_text(
                json.dumps(monitor.snapshot(), ensure_ascii=False), encoding="utf-8",
            )
            monitor.close()


if __name__ == "__main__":
    main()
