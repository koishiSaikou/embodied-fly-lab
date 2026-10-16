"""Millimetre-scale arena, body-based sensors and an explicit engineering policy."""
from dataclasses import dataclass, field, asdict
import math
import numpy as np
import mujoco as mj
from arena_elements import KINDS, MAX_ELEMENTS, initial_elements, sample_fields

from behavior_model import AdaptiveBehavior, BehaviorControls, wind_sample
from arena_appearance import PARTS_PER_ELEMENT, part_name, prop_parts, bounding_radius

from play_fly import (
    Simulation, BodySegment, ContactBodiesPreset, FlatGroundWorld, Rotation3D,
    make_locomotion_fly, PreprogrammedSteps, HybridTurningController,
    LocomotionAction, apply_locomotion_action,
)


@dataclass
class HabitatConfig:
    half_size: float = 25.0
    food: list = field(default_factory=lambda: [12.0, 10.0])
    food_radius: float = 2.5
    odor_sigma: float = 13.0
    spawn: list = field(default_factory=lambda: [-15.0, -10.0])
    odor_enabled: bool = True
    contact_enabled: bool = True
    taste_enabled: bool = True
    vision_enabled: bool = False
    proximity_enabled: bool = True
    multisensory_enabled: bool = False
    light_enabled: bool = True
    humidity_enabled: bool = True
    thermal_enabled: bool = True
    bitter_enabled: bool = True
    elements: list = field(default_factory=list)
    behavior_controls: dict = field(default_factory=lambda: BehaviorControls().snapshot())
    obstacles: list = field(default_factory=lambda: [
        {"type": "circle", "x": -3.0, "y": 4.0, "radius": 3.0},
        {"type": "box", "x": 7.0, "y": -6.0, "hx": 1.5, "hy": 4.0},
    ])

    def __post_init__(self):
        if not self.elements:self.elements=initial_elements(self.food,self.food_radius,self.multisensory_enabled)

    def snapshot(self):
        return asdict(self)

    def valid_food(self, point):
        if len(point) != 2 or not np.isfinite(point).all() or max(abs(float(v)) for v in point) > self.half_size - 4:
            return False
        x, y = point
        for obs in self.obstacles:
            if obs["type"] == "circle":
                if math.hypot(x-obs["x"], y-obs["y"]) < obs["radius"] + self.food_radius + 1:
                    return False
            elif abs(x-obs["x"]) < obs["hx"]+self.food_radius+1 and abs(y-obs["y"]) < obs["hy"]+self.food_radius+1:
                return False
        return True

    def valid_spawn(self, point):
        if len(point)!=2 or not np.isfinite(point).all() or max(abs(float(v)) for v in point)>self.half_size-2:
            return False
        x,y=point
        for ob in self.obstacles:
            if ob["type"]=="circle":
                if math.hypot(x-ob["x"],y-ob["y"])<ob["radius"]+1.5:
                    return False
            elif abs(x-ob["x"])<ob["hx"]+1.5 and abs(y-ob["y"])<ob["hy"]+1.5:
                return False
        return True


class HabitatWorld(FlatGroundWorld):
    def __init__(self, config):
        super().__init__(name="foraging_arena", half_size=30)
        self.config = config
        self.solid_names = []
        h = config.half_size
        for name, pos, size in [
            ("wall_n", (0,h+.5,2), (h+1,.5,2)),
            ("wall_s", (0,-h-.5,2), (h+1,.5,2)),
            ("wall_e", (h+.5,0,2), (.5,h,2)),
            ("wall_w", (-h-.5,0,2), (.5,h,2)),
        ]:
            self.add_solid(name, mj.mjtGeom.mjGEOM_BOX, pos, size, (.38,.52,.63,.5))
        for i, obs in enumerate(config.obstacles):
            if obs["type"] == "circle":
                self.add_solid(f"obstacle_{i}", mj.mjtGeom.mjGEOM_CYLINDER,
                               (obs["x"],obs["y"],1.2), (obs["radius"],1.2,0), (.43,.49,.43,1))
            else:
                self.add_solid(f"obstacle_{i}", mj.mjtGeom.mjGEOM_BOX,
                               (obs["x"],obs["y"],1.2), (obs["hx"],obs["hy"],1.2), (.52,.47,.39,1))
        slots={element['slot']:element for element in config.elements}
        for slot in range(MAX_ELEMENTS):
            for part, spec in enumerate(prop_parts(slots.get(slot))):
                self.mjcf_root.worldbody.add_geom(
                    name=part_name(slot,part), type=mj.mjtGeom(spec['type']),
                    pos=spec['pos'], size=spec['size'], quat=spec['quat'],
                    rgba=spec['rgba'], contype=0, conaffinity=0,
                )
        self.mjcf_root.worldbody.add_camera(
            name="arena_overview", pos=(0,0,76), quat=(1,0,0,0), fovy=44,
        )

    def add_solid(self, name, geom_type, pos, size, rgba):
        geom = self.mjcf_root.worldbody.add_geom(
            name=name, type=geom_type, pos=pos, size=size, rgba=rgba,
            contype=0, conaffinity=0,
        )
        # FlyGym generates explicit collision pairs with every registered solid.
        self.ground_geoms.append(geom)
        self.solid_names.append(name)


def build_habitat(config, seed=0, spawn=None, vision=False):
    fly = make_locomotion_fly(name="fly", add_adhesion=True, colorize=True)
    if vision:
        fly.add_vision()
    cam = fly.add_tracking_camera(
        name="body_cam", pos_offset=(-.5,-7.5,0),
        rotation=Rotation3D("euler", (1.57,0,0)), fovy=35,
    )
    world = HabitatWorld(config)
    world.add_fly(
        fly, [*(spawn or config.spawn),.8], Rotation3D("quat", [1,0,0,0]),
        bodysegs_with_ground_contact=ContactBodiesPreset.LEGS_THORAX_ABDOMEN_HEAD,
        add_ground_contact_sensors=False,
    )
    sim = Simulation(world)
    steps = PreprogrammedSteps()
    order = fly.get_actuated_jointdofs_order("position")
    # 2 kHz controller, with MuJoCo still integrated at 10 kHz.
    controller = HybridTurningController(timestep=sim.timestep*5, preprogrammed_steps=steps, output_dof_order=order)
    controller.reset(seed=seed)
    initial = LocomotionAction(steps.default_pose_by_dof_order(order), np.ones(6,dtype=bool))
    apply_locomotion_action(sim, fly.name, initial)
    sim.warmup()
    return fly, sim, cam, controller


class HabitatSensors:
    def __init__(self, config, fly, sim):
        self.config, self.fly, self.sim = config, fly, sim
        order = fly.get_bodysegs_order()
        self.thorax = order.index(BodySegment("c_thorax"))
        self.head=order.index(BodySegment("c_head"))
        self.antennas = [order.index(BodySegment(f"{s}_funiculus")) for s in ("l","r")]
        self.feet = [order.index(BodySegment(f"{s}_tarsus5")) for s in ("lf","lm","lh","rf","rm","rh")]
        self.thorax_body_id = sim._internal_bodyids_by_fly[fly.name][self.thorax]
        self.solids = {mj.mj_name2id(sim.mj_model,mj.mjtObj.mjOBJ_GEOM,n) for n in sim.world.solid_names}
        self.zone_geoms={slot:[mj.mj_name2id(sim.mj_model,mj.mjtObj.mjOBJ_GEOM,part_name(slot,p))
                               for p in range(PARTS_PER_ELEMENT)] for slot in range(MAX_ELEMENTS)}

    def sync_elements(self):
        slots={element['slot']:element for element in self.config.elements}
        model=self.sim.mj_model
        for slot,geoms in self.zone_geoms.items():
            for geom,spec in zip(geoms,prop_parts(slots.get(slot))):
                model.geom_type[geom]=spec['type']
                model.geom_pos[geom]=spec['pos']
                model.geom_size[geom]=spec['size']
                model.geom_quat[geom]=spec['quat']
                model.geom_rgba[geom]=spec['rgba']
                model.geom_rbound[geom]=bounding_radius(spec)
        mj.mj_forward(model,self.sim.mj_data)

    def concentrations(self, xy):
        delta = np.asarray(xy) - self.config.food
        return np.exp(-np.sum(delta*delta,axis=-1)/(2*self.config.odor_sigma**2))

    def read(self):
        sim = self.sim
        positions = sim.get_body_positions(self.fly.name)
        pos = positions[self.thorax].copy()
        rotation = sim.mj_data.xmat[self.thorax_body_id].reshape(3,3)
        heading = rotation[:2,0]
        yaw = math.atan2(heading[1],heading[0])
        antenna_xy = positions[self.antennas,:2]
        feet = positions[self.feet]
        channels=sample_fields(self.config.elements,antenna_xy,feet,self.config.odor_sigma)
        channels['wind']=wind_sample(self.config.behavior_controls,yaw)
        chemical = np.clip(np.array(channels['sugar_odor'])+channels['fruit_odor'],0,1)
        odor = chemical if self.config.odor_enabled else np.zeros(2)
        on_food = channels['sweetness']>0
        effective={**channels}
        if not self.config.odor_enabled:
            for key in ('sugar_odor','fruit_odor','repellent_odor'):effective[key]=[0.,0.]
        for key,flag,neutral in [('light','light_enabled',[.35,.35]),('humidity','humidity_enabled',[0.,0.]),
                                  ('temperature_c','thermal_enabled',[25.,25.]),('bitter','bitter_enabled',0.)]:
            if not self.config.multisensory_enabled or not getattr(self.config,flag):effective[key]=neutral
        if not self.config.multisensory_enabled or not self.config.humidity_enabled:effective['water_contact']=False
        if not self.config.multisensory_enabled or not self.config.behavior_controls['enabled'] or not self.config.behavior_controls['wind_enabled']:
            effective['wind']={**channels['wind'],'strength':0.,'toward_xy':[0.,0.]}
        food_distances=[float(np.linalg.norm(pos[:2]-[e['x'],e['y']])) for e in self.config.elements
                        if KINDS[e['kind']].get('sweetness') and e['strength']>0]
        force = 0.0
        side = 0.0
        contact_count = 0
        for i, contact in enumerate(sim.mj_data.contact):
            if contact.exclude or not ({int(contact.geom1),int(contact.geom2)} & self.solids):
                continue
            wrench = np.zeros(6)
            mj.mj_contactForce(sim.mj_model,sim.mj_data,i,wrench)
            if wrench[0] <= 1e-7:
                continue
            force += float(np.linalg.norm(wrench[:3]))
            rel = contact.pos[:2]-pos[:2]
            side += float(heading[0]*rel[1]-heading[1]*rel[0])
            contact_count += 1
        # Two virtual short-range antenna probes. These are explicitly geometric
        # proximity sensors, not a model of compound-eye vision.
        probes = []
        for sign in (1,-1):
            angle = yaw + sign*.55
            probe = antenna_xy[0 if sign==1 else 1] + 4*np.array([math.cos(angle),math.sin(angle)])
            clearance = min(self.config.half_size-abs(probe[0]),self.config.half_size-abs(probe[1]))
            for ob in self.config.obstacles:
                if ob["type"] == "circle":
                    d = math.hypot(probe[0]-ob["x"],probe[1]-ob["y"])-ob["radius"]
                else:
                    q = np.abs(probe-[ob["x"],ob["y"]])-[ob["hx"],ob["hy"]]
                    d = float(np.linalg.norm(np.maximum(q,0))+min(max(q),0))
                clearance = min(clearance,d)
            probes.append(float(np.clip((1.2-clearance)/1.2,0,1)))
        return {
            "position_mm": pos.tolist(), "yaw_rad": yaw,
            "foreleg_head_distance_mm":np.linalg.norm(positions[[self.feet[0],self.feet[3]]]-positions[self.head],axis=1).tolist(),
            "upright": float(rotation[2,2]), "antenna_xy": antenna_xy.tolist(),
            "odor_raw": chemical.tolist(), "odor": odor.tolist(),
            "food_distance_mm": float(np.linalg.norm(pos[:2]-self.config.food)),
            "on_food": on_food, "taste": on_food and self.config.taste_enabled,
            "channels_raw":channels,"channels":effective,
            "nearest_food_distance_mm":min(food_distances) if food_distances else None,
            "contact_force_un": force, "solid_contacts": contact_count,
            "contact_side": side, "proximity": probes,
        }


class ForagingPolicy:
    """A transparent sensory-to-command policy; no claim of learned foraging."""
    def __init__(self, seed=0):
        self.behavior=AdaptiveBehavior(seed)
        self.rng = np.random.default_rng(seed)
        self.wander = 0.0
        self.feed_remaining = 0.0
        self.feed_cooldown = 0.0
        self.avoid_remaining = 0.0
        self.avoid_sign = 1.0
        self.food_latched = False
        self.backup_remaining = 0.0
        self.contact_age = 0.0
        self.block_age = 0.0
        self.block_origin = None
        self.block_yaw = 0.0
        self.visual_remaining = 0.0
        self.visual_sign = 1.0
        self.hazard_remaining=0.

    @staticmethod
    def command(state, drive, sugar=0.0, turn=0.0):
        drive = np.asarray(drive, dtype=float)
        return {"state":state, "p9_rate_hz":(abs(drive)*100).tolist(),
                "desired_drive":drive.tolist(), "motor_sign":np.sign(drive).tolist(),
                "sugar_rate_hz":sugar, "turn":turn}

    def step(self,sense,config,dt=.02):
        if config.multisensory_enabled and config.behavior_controls['enabled']:
            return self.behavior.step(sense,config,dt,self)
        return self._step_legacy(sense,config,dt)

    def _step_legacy(self, sense, config, dt=.02):
        channels=sense.get('channels',{})
        if config.multisensory_enabled:
            heat=max(0.,(max(channels.get('temperature_c',[25,25]))-25)/12)
            bitter=channels.get('bitter',0.)
            if bitter>.05 or heat>.8:self.hazard_remaining=.3
            if self.hazard_remaining>0:
                self.hazard_remaining=max(0.,self.hazard_remaining-dt)
                self.feed_remaining=0
                return self.command('hazard_escape',[-.55,-.55])
        # A taste-maintained stop, with hysteresis for intermittent foot contact.
        # This is a declared engineering state, not a neural feeding decision.
        if sense["taste"]:
            self.feed_remaining = 1.0
        if self.feed_remaining>0:
            self.feed_remaining = max(0,self.feed_remaining-dt)
            return self.command("food_dwell", [0,0], 200.0*channels.get('sweetness',1.) if sense["taste"] else 0.0)
        self.wander += -self.wander*dt*2 + self.rng.normal()*math.sqrt(dt)*.12
        left_odor,right_odor=sense["odor"]
        concentration = (left_odor+right_odor)/2
        relative_gradient=(left_odor-right_odor)/(left_odor+right_odor+1e-4)
        # Lateral differences shrink near the source. Reduce speed and increase
        # sensitivity using only measured concentration, never target bearing.
        base = .85-.50*float(np.clip((concentration-.60)/.35,0,1))
        gain = 30/(1-concentration+.08)
        limit = min(.45,base-.16)
        turn = float(np.clip(gain*relative_gradient+self.wander*.35,-limit,limit))
        state = "odor_explore" if config.odor_enabled else "explore_without_odor"
        sensory_turn=0.
        if config.multisensory_enabled:
            light=channels.get('light',[.35,.35]);humidity=channels.get('humidity',[0,0])
            repel=channels.get('repellent_odor',[0,0]);temp=channels.get('temperature_c',[25,25])
            sensory_turn=.6*(light[0]-light[1])+.5*(humidity[0]-humidity[1])+2*(repel[1]-repel[0])+.1*(temp[1]-temp[0])
            if max(repel)>.15 or max(temp)>28:state='hazard_avoid'
            elif channels.get('water_contact') and config.humidity_enabled:state='water_contact';base*=.65
            elif abs(sensory_turn)>.01:state='multisensory_explore'
            limit=max(.01,min(.45,base-.12))
            turn=float(np.clip(turn+sensory_turn,-limit,limit))
        left,right = sense["proximity"]
        if config.contact_enabled:
            blocked = bool(sense["solid_contacts"] or (config.proximity_enabled and max(left,right)>.15))
            self.contact_age = max(0,self.contact_age + (dt if sense["solid_contacts"] else -dt))
            if blocked:
                if self.block_origin is None:
                    self.block_origin = np.asarray(sense["position_mm"][:2])
                    self.block_yaw = sense["yaw_rad"]
                    self.block_age = 0.0
                self.block_age += dt
            else:
                self.block_origin = None
                self.block_age = 0.0
            stalled = False
            if self.block_age>.6:
                moved = np.linalg.norm(np.asarray(sense["position_mm"][:2])-self.block_origin)
                rotated = abs(math.atan2(math.sin(sense["yaw_rad"]-self.block_yaw),math.cos(sense["yaw_rad"]-self.block_yaw)))
                stalled = moved<.5 and rotated<.35
            if self.backup_remaining<=0 and (self.contact_age>.16 or stalled):
                self.backup_remaining = .65
                self.avoid_remaining = .9
                self.contact_age = 0.0
                self.block_origin = None
            if self.backup_remaining>0:
                self.backup_remaining = max(0,self.backup_remaining-dt)
                return self.command("backup_reflex", [-.65,-.65])
            if blocked:
                if self.avoid_remaining<=0:
                    side = sense["contact_side"] if sense["solid_contacts"] else left-right
                    self.avoid_sign = -1 if side>0 else 1
                    if abs(side)<.02:
                        self.avoid_sign = float(self.rng.choice([-1,1]))
                self.avoid_remaining = max(self.avoid_remaining,.3)
            if self.avoid_remaining>0:
                self.avoid_remaining = max(0,self.avoid_remaining-dt)
                # Pivot in place instead of driving an almost motionless leg
                # bank into the obstacle. Direction is an explicit CPG reflex;
                # P9 firing still determines the two amplitude magnitudes.
                return self.command("avoid_reflex", [-self.avoid_sign*.65,self.avoid_sign*.65], turn=self.avoid_sign)
        else:
            self.avoid_remaining = self.backup_remaining = self.contact_age = 0.0
            self.block_origin = None
            self.block_age = 0.0
        visual_turn=0.0
        vision=sense.get("vision")
        if config.vision_enabled and vision is not None:
            vl,vr=vision["risk"]
            visual_turn=.35*(vr-vl)
            base*=1-.4*max(vl,vr)
            if max(vl,vr)>.45:
                if self.visual_remaining<=0:
                    self.visual_sign=-1.0 if vl>vr else 1.0
                    if abs(vl-vr)<.08:
                        self.visual_sign=float(self.rng.choice([-1,1]))
                    self.visual_remaining=.6
                else:
                    self.visual_remaining=max(self.visual_remaining,.2)
            if self.visual_remaining>0:
                self.visual_remaining=max(0,self.visual_remaining-dt)
                result=self.command("visual_avoid",[-self.visual_sign*.60,self.visual_sign*.60],turn=self.visual_sign)
                result["visual_turn"]=self.visual_sign
                return result
            turn=float(np.clip(turn+visual_turn,-min(.45,base-.12),min(.45,base-.12)))
        else:
            self.visual_remaining=0.0
        result=self.command(state, np.clip([base-turn,base+turn],.12,1.2), turn=turn)
        result["visual_turn"]=visual_turn
        result['sensory_turn']=sensory_turn
        return result
