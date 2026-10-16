"""Editable arena, scalar fields and real body/retina integration checks."""
from copy import deepcopy
import json
from pathlib import Path
import numpy as np

from arena_elements import ArenaEditor,KINDS,MAX_ELEMENTS,initial_elements,sample_fields
from arena_appearance import PARTS_PER_ELEMENT, prop_parts
from habitat_world import HabitatConfig,HabitatSensors,ForagingPolicy,build_habitat
from habitat_visuals import HabitatVisuals

ROOT=Path(__file__).resolve().parent


def main():
    config=HabitatConfig(multisensory_enabled=True,vision_enabled=True,proximity_enabled=False)
    config.behavior_controls['enabled']=False  # retain legacy arena/feedback assertions
    editor=ArenaEditor(config.elements,config.half_size,config.obstacles)
    original=deepcopy(editor.projected);revision=editor.revision
    invalid=[dict(action='delete',id='food'),dict(action='add',values=dict(kind='fake',x=0,y=0)),
             dict(action='update',id='food',values=dict(radius=float('nan'))),
             dict(action='update',id='food',values=dict(x=True)),
             dict(action='update',id='food',values=dict(x=24)),
             dict(action='update',id='food',values=dict(x=-3,y=4)),
             dict(action='update',id='food',values=dict(x=-15,y=6)),
             dict(action='update',id='food',values=dict(kind='water')),
             dict(action='update',id='missing',values=dict(strength=.5))]
    for payload in invalid:
        try:editor.prepare(payload)
        except (ValueError,TypeError):pass
        else:raise AssertionError(payload)
        assert editor.projected==original and editor.revision==revision
    capacity=ArenaEditor(initial_elements([12,10],2.5),25,[])
    for _ in range(MAX_ELEMENTS-2):capacity.prepare(dict(action='add',values=dict(kind='light',x=0,y=0,radius=.8)))
    assert len(capacity.projected)==MAX_ELEMENTS
    try:capacity.prepare(dict(action='add',values=dict(kind='light',x=0,y=0,radius=.8)))
    except ValueError:pass
    else:raise AssertionError('Capacity guard')
    assert len({e['slot'] for e in capacity.projected})==MAX_ELEMENTS

    sample=lambda kind:sample_fields([dict(id='test',kind=kind,x=0,y=0,radius=2,strength=1)],
                                      [[-.5,0],[.5,0]],[[0,0,0]])
    assert sample('sugar')['sweetness']==1 and sample('fruit')['sweetness']==.6
    assert sample('fruit')['fruit_odor'][0]>0 and sample('sugar')['fruit_odor']==[0,0]
    assert sample('water')['water_contact'] and max(sample('water')['humidity'])>.9
    assert sample('danger')['bitter']==1 and max(sample('danger')['temperature_c'])>35
    assert max(sample('light')['light'])>.8 and max(sample('shade')['light'])<.1

    fly,sim,cam,controller=build_habitat(config,1,vision=True)
    sensors=HabitatSensors(config,fly,sim);sensors.sync_elements();visuals=HabitatVisuals(sim)
    assert len(visuals.dynamic_indices)==MAX_ELEMENTS*PARTS_PER_ELEMENT
    qpos=sim.mj_data.qpos.copy();model_time=float(sim.mj_data.time)
    initial_png,_=visuals.retina(fly.name,0)
    def apply(payload):
        prepared=editor.prepare(payload);config.elements=prepared['elements']
        primary=next(e for e in config.elements if e['id']=='food')
        config.food[:]=[primary['x'],primary['y']];config.food_radius=primary['radius']
        sensors.sync_elements()
        assert np.array_equal(qpos,sim.mj_data.qpos) and float(sim.mj_data.time)==model_time
        return prepared
    apply(dict(action='add',values=dict(kind='fruit',x=-8,y=-10,radius=1.5)))
    edited_png,_=visuals.retina(fly.name,0)
    assert initial_png!=edited_png,'Real eye rendering must reflect edits without stepping/resetting the fly'
    position=sensors.read()['position_mm']
    payload=apply(dict(action='add',values=dict(kind='danger',x=position[0],y=position[1],radius=3)))
    danger_id=next(e['id'] for e in payload['elements'] if e['slot']==8)
    danger=sensors.read();assert danger['channels']['bitter']>0
    assert ForagingPolicy(1).step(danger,config)['state']=='hazard_escape'
    apply(dict(action='delete',id=danger_id))
    payload=apply(dict(action='add',values=dict(kind='water',x=position[0],y=position[1],radius=3)))
    water_id=next(e['id'] for e in payload['elements'] if e['slot']==8)
    wet=sensors.read();assert wet['channels']['water_contact']
    assert ForagingPolicy(1).step(wet,config)['state']=='water_contact'
    config.humidity_enabled=False
    disabled=sensors.read();assert disabled['channels']['humidity']==[0,0] and not disabled['channels']['water_contact']
    assert disabled['channels_raw']['water_contact']
    apply(dict(action='delete',id=water_id))
    apply(dict(action='update',id='food',values=dict(x=position[0],y=position[1],radius=3,kind='fruit')))
    fruit=sensors.read();command=ForagingPolicy(1).step(fruit,config)
    assert fruit['taste'] and command['state']=='food_dwell' and command['sugar_rate_hz']==120
    # Reuse one live slot across every primitive mix; no reset or new model.
    for kind in KINDS:
        apply(dict(action='update',id='control',values=dict(kind=kind)))
        expected=prop_parts(next(e for e in config.elements if e['id']=='control'))
        for geom,spec in zip(sensors.zone_geoms[1],expected):
            assert int(sim.mj_model.geom_type[geom])==spec['type']
            assert np.allclose(sim.mj_model.geom_size[geom],spec['size'])
            assert not sim.mj_model.geom_contype[geom] and not sim.mj_model.geom_conaffinity[geom]
        visuals.retina(fly.name,0)  # Renderer must support edited primitive types too.
    pose=visuals.pose(0)
    assert len(pose['appearance'])==MAX_ELEMENTS*PARTS_PER_ELEMENT
    assert any(a['rgba'][3]==0 for a in pose['appearance'])
    output=ROOT/'output/arena';output.mkdir(parents=True,exist_ok=True)
    (output/'initial_retina.png').write_bytes(initial_png);(output/'edited_retina.png').write_bytes(edited_png)
    report=dict(status='passed',invalid_edits_rejected=len(invalid),capacity_guard=MAX_ELEMENTS,
                distinct_sugar_fruit_channels=True,real_foot_sweet_bitter_water=True,
                humidity_switch_preserves_raw_sample=True,proxy_light_temperature=True,
                editor_preserves_body_pose_and_time=True,real_retina_updates=True,
                logical_slots=MAX_ELEMENTS,dynamic_primitive_parts=len(pose['appearance']),all_prop_kinds_editable=True,prop_kinds=len(KINDS),fruit_sugar_stimulus_hz=120)
    (output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
