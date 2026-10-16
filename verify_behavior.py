"""Deterministic sensory/learning checks plus actual actuator grooming physics."""
from copy import deepcopy
import json
from pathlib import Path
import numpy as np
from behavior_model import BehaviorControls,validate_needs,wind_sample
from habitat_world import HabitatConfig,ForagingPolicy,HabitatSensors,build_habitat
from habitat_visuals import HabitatVisuals
from visual_navigation import VisualNavigator
import math
from grooming_motor import GroomingMotor
from play_fly import HybridControllerObservation,apply_locomotion_action

OUT=Path(__file__).resolve().parent/'output/behavior'


def sense(**changes):
    result=dict(position_mm=[0,0,.8],yaw_rad=0.,upright=1.,taste=False,odor=[0.,0.],proximity=[0,0],solid_contacts=0,contact_side=0.,
        channels=dict(sugar_odor=[0.,0.],fruit_odor=[0.,0.],repellent_odor=[0,0],sweetness=0.,bitter=0.,
                      temperature_c=[25.,25.],humidity=[0,0],light=[.35,.35],water_contact=False,wind=dict(strength=0.)))
    for key,value in changes.items():
        if key=='channels':result[key].update(value)
        else:result[key]=value
    return result


def run():
    controls=BehaviorControls();invalid=[dict(enabled=1),dict(mode='fake'),dict(bright_threshold=float('nan')),dict(wind_strength=True),dict(wind_direction_deg=361),dict(unknown=1)]
    for values in invalid:
        try:controls.updated(values)
        except ValueError:pass
        else:raise AssertionError(values)
    for values in [dict(hunger=-.1),dict(thirst=True),dict(dust=float('inf')),dict(x=1)]:
        try:validate_needs(values)
        except ValueError:pass
        else:raise AssertionError(values)
    config=HabitatConfig(multisensory_enabled=True,contact_enabled=True,vision_enabled=False)
    def policy(mode='auto',**settings):
        c=deepcopy(config);c.behavior_controls=controls.updated(dict(mode=mode,**settings)).snapshot();return ForagingPolicy(1),c
    p,c=policy('light',light_preference='approach');left_light=sense(channels=dict(light=[.7,.2]))
    approach=p.step(left_light,c);assert approach['state']=='seek_light' and approach['turn']>0
    p,c=policy('light',light_preference='avoid');avoid=p.step(left_light,c);assert avoid['state']=='avoid_bright' and avoid['turn']<0
    p,c=policy('light');assert p.step(sense(channels=dict(light=[.95,.3])),c)['state']=='avoid_bright'
    p,c=policy();thermal=p.step(sense(channels=dict(temperature_c=[30,25])),c);assert thermal['state']=='thermal_seek' and thermal['turn']<0
    p,c=policy('water');wet=sense(channels=dict(water_contact=True,humidity=[1,1]));before=p.behavior.needs['thirst']
    for _ in range(20):assert p.step(wet,c)['state']=='drink'
    assert p.behavior.needs['thirst']<before
    assert p.step(sense(channels=dict(humidity=[.8,.2])),c)['state']=='seek_water'
    c.humidity_enabled=False;assert p.step(wet,c)['state']!='drink'
    p,c=policy();food=sense(taste=True,channels=dict(sweetness=1.,sugar_odor=[.8,.8]));before=p.behavior.needs['hunger']
    for _ in range(50):assert p.step(food,c)['state']=='food_dwell'
    assert p.behavior.needs['hunger']<before and p.behavior.values[0]>.5
    assert p.step(sense(),c)['state']=='food_dwell'  # intermittent contact retains the one-second dwell
    p.behavior.set_needs(dict(hunger=0));assert p.step(food,c)['state']!='food_dwell'
    p,c=policy();p.behavior.eligibility[:]=[1,0];initial=p.behavior.values.copy();p.behavior.reinforce(1,c.behavior_controls);assert p.behavior.values[0]>initial[0] and p.behavior.values[1]==initial[1]
    p.behavior.reinforce(-1,c.behavior_controls);assert p.behavior.values[0]<initial[0]
    p.behavior.clear_memory();assert np.all(p.behavior.values==.5) and not p.behavior.eligibility.any()
    p,c=policy('forage',wind_enabled=False);p.step(sense(channels=dict(sugar_odor=[.5,.5])),c)
    assert p.step(sense(),c)['state']=='odor_search'
    p,c=policy('forage',wind_enabled=False);p.step(sense(channels=dict(sugar_odor=[.09,.09])),c)
    assert p.step(sense(channels=dict(sugar_odor=[.079,.079])),c)['state']=='odor_search'
    assert p.step(sense(channels=dict(sugar_odor=[.2,.2])),c)['state']!='odor_search'
    p,c=policy('forage');wind=wind_sample(c.behavior_controls,0.)
    assert p.step(sense(channels=dict(sugar_odor=[.3,.3],wind=wind)),c)['state']=='upwind_track'
    p,c=policy();c.vision_enabled=True
    surprise=sense(vision=dict(expansion=[2.,0.],dark_occupancy=[.5,.1],risk=[.8,.1]))
    startled=p.step(surprise,c);assert startled['state']=='visual_startle' and max(startled['desired_drive'])<0
    p,c=policy('groom');assert p.step(sense(),c)['state']=='groom'
    assert p.step(sense(channels=dict(bitter=1)),c)['state']=='hazard_escape'
    p,c=policy('rest');assert p.step(sense(),c)['state']=='rest'
    p,c=policy();p.behavior.set_needs(dict(fatigue=.9));assert p.step(sense(),c)['state']=='rest'
    old=ForagingPolicy(1);c.behavior_controls['enabled']=False
    assert old.step(food,c)['state']=='food_dwell'
    # Positive/negative odor training changes the subsequent command, not just a label.
    preference=sense(channels=dict(sugar_odor=[.20,.18],fruit_odor=[.18,.20]))
    p,c=policy('forage',wind_enabled=False,homeostasis_enabled=False,learning_rate=1.)
    p.behavior.eligibility[:]=[1,0]
    for _ in range(5):p.behavior.reinforce(1,c.behavior_controls)
    good=p.step(preference,c)['turn']
    p.behavior.eligibility[:]=[1,0]
    for _ in range(5):p.behavior.reinforce(-1,c.behavior_controls)
    bad=p.step(preference,c)['turn'];assert good>0 and bad<0,(good,bad)

    # Real body, five support feet and a right-front sweep; no editing live qpos.
    body_config=HabitatConfig(multisensory_enabled=True,vision_enabled=True)
    fly,sim,_,controller=build_habitat(body_config,1,vision=True)
    order=fly.get_bodysegs_order();head=next(i for i,j in enumerate(order) if j.name=='c_head');foot=next(i for i,j in enumerate(order) if j.name=='rf_tarsus5');thorax=next(i for i,j in enumerate(order) if j.name=='c_thorax')
    for tick in range(3000):
        if tick%5==0:apply_locomotion_action(sim,fly.name,controller.step([0,0],HybridControllerObservation.from_sim(sim,fly.name)))
        sim.step()
    positions=sim.get_body_positions(fly.name);before=float(np.linalg.norm(positions[foot]-positions[head]))
    sensors=HabitatSensors(body_config,fly,sim);visuals=HabitatVisuals(sim)
    png_before,eyes=visuals.retina(fly.name,0.);visual=VisualNavigator(sim.retina);visual.update(eyes['samples'],0.)
    actual=sensors.read();x,y=actual['position_mm'][:2];yaw=actual['yaw_rad']
    body_config.elements.append(dict(id='startle_fixture',kind='fruit',label='startle fixture',x=x+4*math.cos(yaw),y=y+4*math.sin(yaw),radius=3.2,strength=1.,slot=7))
    sensors.sync_elements();png_after,eyes=visuals.retina(fly.name,.04);actual_visual=visual.update(eyes['samples'],.04)
    actual=sensors.read();actual['vision']=actual_visual
    assert ForagingPolicy(1).step(actual,body_config)['state']=='visual_startle'
    real_expansion=max(actual_visual['expansion']);assert real_expansion>1.2
    body_config.elements.pop();sensors.sync_elements()
    antenna=sensors.read()['antenna_xy'][0]
    body_config.elements.append(dict(id='cold_fixture',kind='cool',label='cold fixture',x=antenna[0],y=antenna[1],radius=2.5,strength=1.,slot=7));sensors.sync_elements()
    assert min(sensors.read()['channels_raw']['temperature_c'])<20
    body_config.behavior_controls['wind_enabled']=False;wind_off=sensors.read();assert wind_off['channels']['wind']['strength']==0 and wind_off['channels_raw']['wind']['strength']>0
    body_config.behavior_controls['wind_enabled']=True;body_config.behavior_controls['enabled']=False;assert sensors.read()['channels']['wind']['strength']==0
    body_config.behavior_controls['enabled']=True;body_config.elements.pop();sensors.sync_elements()
    motor=GroomingMotor(fly,sim,sim.timestep*5);qpos=sim.mj_data.qpos.copy();stamp=sim.mj_data.time;motor.prepare()
    assert np.array_equal(qpos,sim.mj_data.qpos) and sim.mj_data.time==stamp
    motor.targets=None  # exercise the settle-before-planning path used by live grooming
    upright=1.;closest=before
    for tick in range(14000):
        if tick%5==0:
            action=controller.step([0,0],HybridControllerObservation.from_sim(sim,fly.name));apply_locomotion_action(sim,fly.name,motor.action(action,True))
        sim.step()
        if tick%100==0:
            positions=sim.get_body_positions(fly.name);closest=min(closest,float(np.linalg.norm(positions[foot]-positions[head])))
            upright=min(upright,float(sim.mj_data.xmat[sim._internal_bodyids_by_fly[fly.name][thorax]].reshape(3,3)[2,2]))
    assert closest<.8 and upright>.9 and max(motor.planning_errors)<.2,(closest,upright)
    action=controller.step([0,0],HybridControllerObservation.from_sim(sim,fly.name));assert motor.action(action,True,allow=False) is action
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'startle_before.png').write_bytes(png_before);(OUT/'startle_after.png').write_bytes(png_after)
    result=dict(status='passed',invalid_controls_rejected=10,light_and_strong_light=True,thermal_gradient=True,water_and_satiety=True,
        actual_reward_changes_steering=[good,bad],odor_loss_search=True,wind_tracking=True,synthetic_retinal_feature_startle=True,actual_retina_expansion=real_expansion,actual_cold_antenna_field=True,wind_raw_and_gated=True,
        safety_overrides_groom=True,legacy_switch_preserved=True,groom_plan_preserves_live_qpos_time=True,
        right_front_head_distance_before_mm=before,right_front_head_closest_mm=closest,groom_minimum_upright=upright,ik_errors_mm=motor.planning_errors,
        feedback_off_disables_groom=True)
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))


if __name__=='__main__':run()
