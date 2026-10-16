"""Foraging arena: explicit engineering feedback, full LIF brain and recordings."""
import argparse
import csv
import gzip
from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR",str(ROOT/".cache"/"matplotlib"))
os.environ.setdefault("NUMBA_CACHE_DIR",str(ROOT/".cache"/"numba"))
os.environ.setdefault("FLYGYM_ASSET_CACHE_DIR",str(ROOT/".cache"/"flygym_assets"))

import mujoco as mj
import mujoco.viewer
import numpy as np
from habitat_world import HabitatConfig, build_habitat, HabitatSensors, ForagingPolicy
from play_fly import HybridControllerObservation, apply_locomotion_action
from habitat_monitor import HabitatMonitor
from habitat_visuals import HabitatVisuals
from visual_navigation import VisualNavigator
from brain_controls import BrainControls
from arena_elements import ArenaEditor, SENSORY_SWITCHES
from behavior_model import BehaviorControls, validate_needs
from grooming_motor import GroomingMotor
from startup_support import available_port, open_dashboard, defer_interrupts_while_saving
from brain_client import BrainClient, response_timeout
from experiment_recovery import Finalizer, failure_record, write_json
from project_info import project_info

ASSUMPTIONS = {
    "odor":"Static normalized Gaussian field, sampled at two actual antenna segment positions. No fluid dynamics.",
    "odor_to_brain":"Engineering policy converts odor differences to P9 stimulation; olfactory receptor neurons are not implemented.",
    "taste":"Foot position within the floor food disc triggers the upstream sugar-GRN set, without body-site-specific calibration.",
    "avoidance":"Retinal-contrast engineering reflex plus actual contact recovery. Geometric probes are optional and disabled by default.",
    "dwell":"Taste maintains a stop, with 1 s hysteresis after foot contact loss. This is an engineering rule, not neural evidence of feeding.",
    "motor":"Measured P9 rates, smoothed before saturation over 100 ms, set CPG amplitudes. Explicit visual/contact/proximity reflexes set reverse/pivot signs. CPG 2 kHz; physics 10 kHz. No VNC model; the CPG is not learned. External odor-value memory is described separately.",
    "vision":"Actual 721-ommatidium samples per eye drive a horizon-contrast/positive occupancy-change reflex, combined with odor guidance. Features map to P9 input through engineering rules, not a biological optic-lobe pathway. No depth/segmentation/object coordinates are provided to the visual processor.",
    "anatomy":"Only the downloaded FAFB v783 skeleton subset is rendered with activity. Registered neuropil templates remain static anatomical references.",
    "annotations":"FlyWire v783 annotation v3.2.0 labels are joined by exact ID. Identity lookup leaves base weights and LIF equations unchanged. Optional predicted-transmitter source gains scale only temporary effective weights; unknown sources stay at 1. P9 A/B motor channel order is preserved.",
    "transmitter_gains":"W_eff[post,pre] = W_base[post,pre] * gain[top_nt(pre)]. Predicted groups, unchanged base signs, no receptors/concentration/plasticity. Pre-existing delayed currents persist. All-one gains use the exact original weight tensor.",
    "adaptive_behavior":"External engineering needs and learned odor-value memory modulate P9 stimuli; uniform LIF weights remain unchanged. Wind is a scalar/directional proxy, not simulated airflow. Grooming uses a foreleg IK actuator program, not a restored grooming circuit.",
    "multisensory":"Editable floor cues are visible to real eye cameras. Sugar/fruit/repellent odors, humidity, light and temperature are explicit scalar proxy fields at actual antenna positions; foot sweet/bitter/water cues use actual feet. New cues feed engineering P9 policy, not verified sensory neural pathways. Water has no fluid physics; temperature/light fields have no thermal/radiometric solver.",
}


def main(restarting=False):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=float,default=0,
                        help="Model seconds per experiment; 0 (default) runs until manually stopped")
    parser.add_argument("--seed",type=int,default=1)
    parser.add_argument("--no-brain",action="store_true",help="Explicit engineering-only control baseline")
    parser.add_argument("--headless",action="store_true")
    parser.add_argument("--monitor",action="store_true")
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--port",type=int,default=None)
    parser.add_argument("--video",action="store_true")
    parser.add_argument("--keep-open",action="store_true",help="Keep the completed web experiment available until Ctrl+C")
    parser.add_argument("--brain-startup-timeout",type=response_timeout,default=300,
                        help="Brain ready deadline in wall seconds (default: 300)")
    parser.add_argument("--brain-step-timeout",type=response_timeout,default=30,
                        help="One pending brain reply deadline in wall seconds (default: 30; pauses excluded)")
    parser.add_argument("--no-odor",action="store_true")
    parser.add_argument("--no-contact-feedback",action="store_true")
    parser.add_argument("--no-taste",action="store_true")
    parser.add_argument("--no-vision",action="store_true",help="Ablation: keep eye display but disable visual control")
    parser.add_argument("--geometric-probes",action="store_true",help="Opt in to the old geometric near-distance reflex")
    parser.add_argument("--disconnect-brain-output",action="store_true",help="Ablation: zero motor drive while brain keeps running")
    parser.add_argument("--food",type=float,nargs=2,default=[12,10])
    parser.add_argument("--spawn",type=float,nargs=2,default=[-15,-10])
    parser.add_argument("--run-name",default=None)
    parser.add_argument("--arena",choices=['foraging','classic'],default='foraging',help='Rich editable arena or legacy two-zone arena')
    args=parser.parse_args()
    if restarting: args.run_name=None
    if not math.isfinite(args.seconds) or (args.seconds!=0 and not .02<=args.seconds<=3600):
        parser.error("--seconds must be 0 (continuous) or finite and in [.02,3600]")
    if args.monitor and args.no_brain:
        parser.error("Use the monitoring mode with a live brain; --no-brain is a headless engineering baseline")
    if args.port is not None and not 1024<=args.port<=65535:
        parser.error("--port must be in [1024,65535]")
    if args.monitor:
        try:args.port=available_port(args.port if args.port is not None else 8472,automatic=args.port is None)
        except RuntimeError as error:parser.error(str(error))
    if args.run_name and not re.fullmatch(r"[A-Za-z0-9_-]+",args.run_name):
        parser.error("--run-name accepts only ASCII letters, digits, underscores and hyphens")
    config=HabitatConfig(food=args.food,spawn=args.spawn,odor_enabled=not args.no_odor,
                         contact_enabled=not args.no_contact_feedback,taste_enabled=not args.no_taste,
                         vision_enabled=not args.no_vision,proximity_enabled=args.geometric_probes,
                         multisensory_enabled=args.arena=='foraging')
    if not config.valid_food(config.food) or not config.valid_spawn(config.spawn):
        parser.error("Food/spawn must be inside the arena and clear of obstacles")
    try:arena_editor=ArenaEditor(config.elements,config.half_size,config.obstacles)
    except ValueError as error:parser.error(str(error))
    name=args.run_name or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    directory=ROOT/"output"/"habitat"/name
    directory.mkdir(parents=True,exist_ok=False)
    brain_controls=BrainControls()
    print(f"Run directory: {directory}",flush=True)
    commands=queue.Queue()
    def accept(message):
        kind=message["type"]
        if kind=="food":
            point=[float(message["x"]),float(message["y"])]
            commands.put(arena_editor.prepare(dict(action='update',id='food',values=dict(x=point[0],y=point[1]))))
        elif kind=='arena_edit':
            commands.put(arena_editor.prepare(message))
        elif kind=='sensory_settings':
            values=message.get('values')
            if not isinstance(values,dict) or not values or any(key not in SENSORY_SWITCHES or type(value) is not bool for key,value in values.items()):
                raise ValueError('感觉开关必须是受支持的布尔值')
            commands.put(dict(type=kind,values=dict(values)))
        elif kind=='behavior_settings':
            BehaviorControls(**config.behavior_controls).updated(message.get('values'))
            commands.put(dict(type=kind,values=dict(message['values'])))
        elif kind=='behavior_needs':
            commands.put(dict(type=kind,values=validate_needs(message.get('values'))))
        elif kind=='behavior_memory_reset':commands.put(dict(type=kind))
        elif kind=='behavior_reward':
            reward=message.get('reward')
            if type(reward) not in (int,float) or not math.isfinite(reward) or not -1<=reward<=1:raise ValueError('训练奖励须为 -1–1')
            commands.put(dict(type=kind,reward=float(reward)))
        elif kind in ("stop","restart"):
            commands.put({"type":kind})
        elif kind=="brain_settings":
            changes=message.get("values")
            brain_controls.updated(changes)
            commands.put({"type":kind,"values":dict(changes)})
        elif kind in ("odor","contact","taste","vision","pause") and isinstance(message.get("enabled"),bool):
            commands.put({"type":kind,"enabled":message["enabled"]})
        else:
            raise ValueError("Unknown command")
    monitor=None
    client=None
    viewer=None
    renderer=None
    writer=None
    status="error"
    failure=None
    finalizer=Finalizer()
    rows=[]
    trail=[]
    metrics={"path_mm":0.0,"food_zone_time_s":0.0,"food_dwell_time_s":0.0,"near_wall_time_s":0.0,
             "contact_episodes":0,"first_food_time_s":None,"visited_cells":0,"resets":0}
    grid=set()
    paused=False
    actual_time=0.0
    previous_contact=False
    last_state=None
    completed_steps=0
    next_render=0.0
    next_retina=0.0
    retina_meta=None
    vision_meta=None
    vision_processor=None
    stop_requested=False
    last_food_distance=None
    last_transmitter_gains=None
    arena_revision=0
    behavior_revision=0
    event_file=neural_file=retina_file=csv_file=senses_file=behavior_file=None
    policy=None;grooming=None
    columns=["time_s","x_mm","y_mm","z_mm","yaw_rad","odor_left","odor_right","on_food",
             "contact_force_un","solid_contacts","state","input_p9_left_hz","input_p9_right_hz",
             "actual_p9_left_hz","actual_p9_right_hz","sugar_input_hz","sugar_spikes","brain_spikes","upright",
             "food_x_mm","food_y_mm","food_distance_mm","odor_enabled","avoidance_enabled","taste_enabled",
             "motor_drive_left","motor_drive_right","vision_enabled","geometric_probes_enabled",
             "vision_input_time_s","vision_risk_left","vision_risk_right","visual_turn"]
    def event(kind,**kwargs):
        event_file.write(json.dumps({"time_s":actual_time,"type":kind,**kwargs})+"\n");event_file.flush()
    started=time.perf_counter()
    try:
        write_json(directory/"config.json",{"project":project_info(),"environment":config.snapshot(),"arguments":vars(args),
                                          "brain_controls":brain_controls.snapshot(),"assumptions":ASSUMPTIONS})
        event_file=(directory/"events.jsonl").open("w",encoding="utf-8")
        neural_file=(directory/"neural.jsonl").open("w",encoding="utf-8")
        retina_file=(directory/"retina.jsonl").open("w",encoding="utf-8") if args.monitor or config.vision_enabled else None
        csv_file=(directory/"trajectory.csv").open("w",newline="",encoding="utf-8")
        senses_file=(directory/"senses.jsonl").open("w",encoding="utf-8")
        behavior_file=(directory/"behavior.jsonl").open("w",encoding="utf-8")
        csv_writer=csv.DictWriter(csv_file,fieldnames=columns);csv_writer.writeheader()
        fly,sim,follow_cam,controller=build_habitat(config,args.seed,vision=args.monitor or config.vision_enabled)
        visuals=HabitatVisuals(sim) if args.monitor or config.vision_enabled else None
        def sample_eyes():
            nonlocal retina_meta,vision_meta,vision_processor,next_retina
            png,retina_meta=visuals.retina(fly.name,actual_time)
            if vision_processor is None: vision_processor=VisualNavigator(sim.retina)
            vision_meta=vision_processor.update(retina_meta["samples"],actual_time)
            retina_meta["drives_behavior"]=config.vision_enabled
            retina_meta["navigation"]=vision_meta
            next_retina=actual_time+.04
            if monitor: monitor.set_retina(png,actual_time)
            (directory/"retina.png").write_bytes(png)
            retina_file.write(json.dumps(retina_meta,separators=(',',':'))+"\n")
        sensors=HabitatSensors(config,fly,sim)
        sensors.sync_elements()
        policy=ForagingPolicy(args.seed)
        grooming=GroomingMotor(fly,sim,sim.timestep*5)
        overview_id=mj.mj_name2id(sim.mj_model,mj.mjtObj.mjOBJ_CAMERA,"arena_overview")
        follow_id=mj.mj_name2id(sim.mj_model,mj.mjtObj.mjOBJ_CAMERA,follow_cam.name)
        food_id=mj.mj_name2id(sim.mj_model,mj.mjtObj.mjOBJ_GEOM,"food_zone")
        if args.monitor:
            monitor=HabitatMonitor(args.port,command_callback=accept)
            monitor.lifecycle(directory=str(directory),recovery={"phase":"active","can_restart":False})
            monitor.set_scene(visuals.scene)
            with gzip.open(directory/"scene.json.gz","wt",encoding="utf-8") as stream:
                json.dump(visuals.scene,stream,separators=(',',':'))
            print(f"Habitat: {monitor.url}",flush=True)
            open_dashboard(monitor.url,enabled=not args.no_browser and not restarting)
        if not args.no_brain:
            print("Loading full LIF network with P9 and sugar GRN inputs...",flush=True)
            client=BrainClient(directory,args.seed,startup_timeout=args.brain_startup_timeout,
                               step_timeout=args.brain_step_timeout,
                               progress=(lambda wait:monitor.lifecycle(brain_wait=wait)) if monitor else None)
            if monitor:
                monitor.ready({**client.metadata,"habitat":True})
        def key_callback(key):
            if key==80: commands.put({"type":"pause","enabled":not paused})
            if key==79: commands.put({"type":"odor","enabled":not config.odor_enabled})
            if key==67: commands.put({"type":"contact","enabled":not config.contact_enabled})
            if key==84: commands.put({"type":"taste","enabled":not config.taste_enabled})
            if key in (49,50) and viewer:
                viewer.cam.type=mj.mjtCamera.mjCAMERA_FIXED
                viewer.cam.fixedcamid=overview_id if key==49 else follow_id
        if not args.headless:
            viewer=mujoco.viewer.launch_passive(sim.mj_model,sim.mj_data,key_callback=key_callback)
            viewer.cam.type=mj.mjtCamera.mjCAMERA_FIXED;viewer.cam.fixedcamid=overview_id
        if args.video or args.headless:
            renderer=mj.Renderer(sim.mj_model,height=640,width=640)
        if args.video:
            import imageio.v2 as iio
            writer=iio.get_writer(directory/"arena.mp4",fps=25)
        print("Autonomous engineering feedback. 1 overview / 2 follow / P pause / O odor / C contact / T taste.",flush=True)
        sense=sensors.read(); previous_pos=np.array(sense["position_mm"][:2])
        target_steps=None if args.seconds==0 else int(round(args.seconds/sim.timestep))
        print("Continuous experiment: end and save from the webpage, or press Ctrl+C."
              if target_steps is None else f"Timed experiment: {args.seconds:g} model seconds.",flush=True)
        while (target_steps is None or completed_steps<target_steps) and not stop_requested:
            if viewer and not viewer.is_running(): break
            environment_changed=False
            while not commands.empty():
                message=commands.get_nowait()
                if message["type"]=="stop":
                    stop_requested=True
                elif message['type']=='behavior_settings':
                    before=dict(config.behavior_controls)
                    config.behavior_controls=BehaviorControls(**before).updated(message['values']).snapshot()
                    if before['mode']!=config.behavior_controls['mode'] or not config.behavior_controls['enabled']:
                        policy.behavior.rest_remaining=policy.behavior.groom_remaining=0.
                    environment_changed=True
                    event('behavior_settings',before=before,after=config.behavior_controls)
                elif message['type']=='behavior_needs':
                    before=dict(policy.behavior.needs);policy.behavior.set_needs(message['values'])
                    event('behavior_needs',before=before,after=dict(policy.behavior.needs))
                elif message['type']=='behavior_memory_reset':
                    policy.behavior.clear_memory();event('behavior_memory_reset')
                elif message['type']=='behavior_reward':
                    applied=policy.behavior.reinforce(message['reward'],config.behavior_controls)
                    event('behavior_reward',reward=message['reward'],applied=applied,behavior=policy.behavior.snapshot())
                elif message["type"]=="brain_settings":
                    before=brain_controls.snapshot()
                    brain_controls=brain_controls.updated(message["values"])
                    event("brain_settings",before=before,after=brain_controls.snapshot())
                elif message["type"]=="arena_edit":
                    environment_changed=True
                    config.elements=message['elements'];arena_revision=message['arena_revision']
                    primary=next(element for element in config.elements if element['id']=='food')
                    config.food[:]=[primary['x'],primary['y']];config.food_radius=primary['radius']
                    sensors.sync_elements()
                    policy.food_latched=False;policy.feed_remaining=0
                    event('arena_edit',elements=config.elements,arena_revision=arena_revision,request=message['request'])
                elif message['type']=='sensory_settings':
                    environment_changed=True
                    for key,value in message['values'].items():setattr(config,key,value)
                elif message["type"]=="pause": paused=message["enabled"]
                elif message["type"]!="restart":
                    environment_changed=True
                    setattr(config,{"odor":"odor_enabled","contact":"contact_enabled","taste":"taste_enabled","vision":"vision_enabled"}[message["type"]],message["enabled"])
                if message["type"].startswith("behavior_"):behavior_revision+=1
                event("command",command=message)
            if stop_requested: break
            if paused:
                if viewer: viewer.sync()
                if monitor:
                    snap=monitor.snapshot().get("habitat")
                    if snap:
                        if environment_changed:
                            sample_eyes()
                        monitor.habitat_update({**snap,"paused":True,"config":config.snapshot(),
                                                "brain_controls":brain_controls.snapshot(),
                                                "behavior":policy.behavior.snapshot(config.behavior_controls["enabled"] and config.multisensory_enabled),"behavior_revision":behavior_revision,
                                                "arena_revision":arena_revision,
                                                "sense":{**sensors.read(),"vision":vision_meta},"pose":visuals.pose(actual_time),"retina":retina_meta})
                time.sleep(.05);continue
            sense=sensors.read()
            if visuals and (retina_meta is None or actual_time+1e-8>=next_retina): sample_eyes()
            sense["vision"]=vision_meta
            nsteps=200 if target_steps is None else min(200,target_steps-completed_steps)
            chunk_s=nsteps*sim.timestep
            command=brain_controls.encode(policy.step(sense,config,chunk_s))
            if client:
                chunk=client.advance(command,chunk_s*1000,bool(monitor),brain_controls.smoothing_ms,
                                     brain_controls.transmitter_gains())
                last_transmitter_gains=chunk.get("transmitter_gains")
                drive=np.array(brain_controls.motor_drive(chunk["drive"],command["motor_sign"]))
                neural_file.write(json.dumps({k:v for k,v in chunk.items() if k!="activity"})+"\n")
            else:
                chunk={"p9_hz":[None,None],"sugar_spikes":None,"total_spikes":None}
                drive=np.array(brain_controls.motor_drive(abs(np.array(command["desired_drive"])),command["motor_sign"]))
            if args.disconnect_brain_output: drive[:]=0
            for tick in range(nsteps):
                if tick%5==0:
                    obs=HybridControllerObservation.from_sim(sim,fly.name)
                    motor_action=controller.step(drive,obs)
                    motor_action=grooming.action(motor_action,command['state']=='groom',
                        allow=brain_controls.feedback_enabled and brain_controls.motor_gain>0 and not args.disconnect_brain_output and config.behavior_controls['enabled'] and config.behavior_controls['groom_enabled'],
                        emergency=command['state'] in ('hazard_escape','backup_reflex','avoid_reflex','visual_avoid','visual_startle'))
                    apply_locomotion_action(sim,fly.name,motor_action)
                sim.step()
                if not np.isfinite(sim.mj_data.qpos).all(): raise RuntimeError("Non-finite body state")
            completed_steps+=nsteps;actual_time=completed_steps*sim.timestep
            new_sense=sensors.read();pos=np.array(new_sense["position_mm"][:2])
            new_sense["vision"]=vision_meta
            behavior_file.write(json.dumps(dict(time_s=actual_time,controls=config.behavior_controls,**policy.behavior.snapshot(config.behavior_controls["enabled"] and config.multisensory_enabled)))+"\n")
            senses_file.write(json.dumps(dict(time_s=actual_time,channels=new_sense['channels'],
                                               channels_raw=new_sense['channels_raw'],arena_revision=arena_revision))+'\n')
            metrics["path_mm"]+=float(np.linalg.norm(pos-previous_pos));previous_pos=pos
            metrics["food_zone_time_s"]+=chunk_s*int(new_sense["on_food"])
            metrics["food_dwell_time_s"]+=chunk_s*int(command["state"]=="food_dwell")
            metrics["near_wall_time_s"]+=chunk_s*int(max(abs(pos))>config.half_size-3)
            contact=bool(new_sense["solid_contacts"])
            if contact and not previous_contact:
                metrics["contact_episodes"]+=1;event("solid_contact",force_un=new_sense["contact_force_un"])
            previous_contact=contact
            if new_sense["on_food"] and metrics["first_food_time_s"] is None:
                metrics["first_food_time_s"]=actual_time;event("first_food_contact")
            if command["state"]!=last_state:
                event("state",state=command["state"]);last_state=command["state"]
            grid.add((int((pos[0]+25)//2),int((pos[1]+25)//2)));metrics["visited_cells"]=len(grid)
            trail.append([actual_time,float(pos[0]),float(pos[1]),command["state"]])
            row=dict(zip(columns,[actual_time,*new_sense["position_mm"],new_sense["yaw_rad"],*sense["odor"],
                                  int(new_sense["on_food"]),new_sense["contact_force_un"],new_sense["solid_contacts"],command["state"],
                                  *command["p9_rate_hz"],*chunk["p9_hz"],command["sugar_rate_hz"],chunk["sugar_spikes"],
                                  chunk["total_spikes"],new_sense["upright"],*config.food,new_sense["food_distance_mm"],
                                  int(config.odor_enabled),int(config.contact_enabled),int(config.taste_enabled),*drive,
                                  int(config.vision_enabled),int(config.proximity_enabled),
                                  vision_meta["time_s"] if vision_meta else None,
                                  *(vision_meta["risk"] if vision_meta else [None,None]),command.get("visual_turn",0)]))
            csv_writer.writerow(row);rows.append(row)
            if monitor:
                monitor.update(chunk,habitat={"config":config.snapshot(),"sense":new_sense,"command":command,"motor_drive":drive.tolist(),
                                        "metrics":dict(metrics),"trail":trail[-3000:],"time_s":actual_time,
                                        "paused":False,"directory":str(directory),"assumptions":ASSUMPTIONS,
                                        "brain_controls":brain_controls.snapshot(),
                                        "behavior":policy.behavior.snapshot(config.behavior_controls["enabled"] and config.multisensory_enabled),"behavior_revision":behavior_revision,
                                        "brain_feedback_disconnected":args.disconnect_brain_output,
                                        "arena_revision":arena_revision,
                                        "pose":visuals.pose(actual_time),"retina":retina_meta})
            if viewer: viewer.sync()
            if renderer and actual_time>=next_render:
                renderer.update_scene(sim.mj_data,camera=overview_id)
                image=renderer.render()
                if writer: writer.append_data(image)
                next_render=actual_time+.04
            if int(actual_time*50)%50==0:
                csv_file.flush();neural_file.flush()
                print(f"Model {actual_time:.1f}s | {command['state']} | food {new_sense['food_distance_mm']:.1f}mm | path {metrics['path_mm']:.1f}mm",flush=True)
            if max(abs(pos))>config.half_size+3:
                raise RuntimeError("Fly left the enclosure; recorded without teleporting or silently resetting")
            last_food_distance=new_sense["food_distance_mm"]
        status="finished"
    except KeyboardInterrupt:
        status="interrupted"
        print("Stopped by user; saving experiment records...",flush=True)
    except Exception as error:
        failure=failure_record(error)
        print(f"实验停止：{failure['message']}",file=sys.stderr,flush=True)
        print(failure["traceback"],file=sys.stderr,flush=True)
    finally:
        with defer_interrupts_while_saving() as saving_interrupt:
            if monitor:
                monitor.lifecycle(status="saving",failure=failure,brain_wait=None,
                                  recovery={"phase":"saving","can_restart":False})
            if viewer: finalizer.attempt("viewer_close",viewer.close)
            if renderer:
                def save_image():
                    import imageio.v3 as iio
                    renderer.update_scene(sim.mj_data,camera=overview_id)
                    iio.imwrite(directory/"arena.png",renderer.render())
                finalizer.attempt("save_image",save_image)
                finalizer.attempt("renderer_close",renderer.close)
            if writer: finalizer.attempt("video_close",writer.close)
            if client: finalizer.attempt("brain_close",client.close)
            for f in (csv_file,event_file,neural_file,retina_file,senses_file,behavior_file):
                if f: finalizer.attempt("close_"+Path(f.name).name,f.close)
            if rows: finalizer.attempt("save_plots",lambda:save_plots(rows,config,directory))
            if finalizer.errors and status=="finished":status="error"
            report={"status":status,"body_model_seconds":actual_time,"wall_seconds":time.perf_counter()-started,
                    "project":project_info(),
                    "failure":failure,"cleanup_errors":finalizer.errors,
                    "last_successful_brain_time_ms":client.last_brain_time_ms if client else (failure or {}).get("details",{}).get("last_brain_time_ms",0),
                    "brain_controls_final":brain_controls.snapshot(),
                    "behavior_revision":behavior_revision,
                    "behavior_final":policy.behavior.snapshot(config.behavior_controls["enabled"] and config.multisensory_enabled) if policy else None,
                    "groom_ik_errors_mm":grooming.planning_errors if grooming else [],
                    "brain_effective_transmitter_gains":last_transmitter_gains,
                    "arena_final":config.snapshot(),"arena_revision":arena_revision,
                    "brain_enabled":not args.no_brain,"seed":args.seed,"metrics":metrics,
                    "final_food_distance_mm":last_food_distance,"minimum_upright":min((r["upright"] for r in rows),default=None),
                    "brain_metadata":client.metadata if client else None,"assumptions":ASSUMPTIONS,
                    "visuals":{"retina_frames":retina_meta["frame_index"],"last_retina_time_s":retina_meta["time_s"],
                               "ommatidia_per_eye":retina_meta["ommatidia_per_eye"]} if retina_meta else None}
            finalizer.attempt("save_report",lambda:write_json(directory/"report.json",report))
            if finalizer.errors and status=="finished":status="error"
            report["status"]=status
            if monitor:
                monitor.lifecycle(status=status,failure=failure,cleanup_errors=finalizer.errors,
                                  recovery={"phase":"complete","can_restart":False})
                finalizer.attempt("save_snapshot",lambda:write_json(directory/"monitor_snapshot.json",monitor.snapshot()))
            if finalizer.errors:
                if status=="finished":status="error"
                report["status"]=status
                # An independent error file still works if just report.json is unwritable.
                finalizer.attempt("save_error",lambda:write_json(directory/"error.json",report))
                finalizer.attempt("retry_report",lambda:write_json(directory/"report.json",report))
            can_restart=bool(monitor and args.keep_open and status in ("finished","error")
                             and not (failure or {}).get("details",{}).get("cleanup_failed")
                             and not any(e["stage"]=="brain_close" for e in finalizer.errors))
            if monitor:
                monitor.lifecycle(status=status,failure=failure,cleanup_errors=list(finalizer.errors),
                                  recovery={"phase":"complete","can_restart":can_restart})
                if not args.keep_open or status=="interrupted":finalizer.attempt("monitor_close",monitor.close)
            print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        if monitor and saving_interrupt["requested"]: finalizer.attempt("monitor_close",monitor.close)
        if monitor and args.keep_open and status in ("finished","error") and not saving_interrupt["requested"]:
            print(f"Experiment stopped ({status}). Dashboard remains at {monitor.url}; start a new round explicitly or Ctrl+C to close.",flush=True)
            try:
                while True:
                    try: message=commands.get(timeout=.5)
                    except queue.Empty: continue
                    if message["type"]=="restart" and can_restart:
                        monitor.close()
                        return True
            except KeyboardInterrupt:
                monitor.close()
        if status=="error" or finalizer.errors:raise SystemExit(1)


def save_plots(rows,config,directory):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle,Rectangle
    x=[r["x_mm"] for r in rows];y=[r["y_mm"] for r in rows]
    fig,ax=plt.subplots(figsize=(6,6))
    ax.plot(x,y,linewidth=.8,color="#146c7d");ax.scatter(x[0],y[0],c="green",s=25,label="start");ax.scatter(x[-1],y[-1],c="red",s=25,label="end")
    ax.add_patch(Circle(config.food,config.food_radius,color="orange",alpha=.6))
    for ob in config.obstacles:
        ax.add_patch(Circle((ob['x'],ob['y']),ob['radius'],color="gray") if ob['type']=="circle" else
                     Rectangle((ob['x']-ob['hx'],ob['y']-ob['hy']),2*ob['hx'],2*ob['hy'],color="gray"))
    ax.set(xlim=(-25,25),ylim=(-25,25),xlabel="x / mm",ylabel="y / mm",title="Measured body trajectory (engineering policy)",aspect="equal");ax.legend()
    fig.tight_layout();fig.savefig(directory/"trajectory.png",dpi=140);plt.close(fig)
    fig,ax=plt.subplots(figsize=(6,5));ax.hist2d(x,y,bins=25,range=[[-25,25],[-25,25]],cmap="magma")
    ax.set(xlabel="x / mm",ylabel="y / mm",title="Occupancy counts (50 Hz body samples)",aspect="equal")
    fig.tight_layout();fig.savefig(directory/"occupancy.png",dpi=140);plt.close(fig)


if __name__=="__main__":
    restarting=False
    while main(restarting):
        restarting=True
