"""Inspectable engineering needs, odor-value learning and sensory arbitration.

This state lives outside the uniform FlyWire LIF network. Learning changes only
two odor preference values, never connectome weights, IDs or cell physiology.
"""
from dataclasses import dataclass, asdict, replace
import math
import numpy as np

MODES={'auto':'综合探索','forage':'优先觅食','water':'优先寻水','light':'跟随明暗偏好',
       'explore':'自由探索','rest':'休息','groom':'前足清洁'}
FIELDS=[dict(key='mode',label='行为模式',kind='choice',choices=MODES,group='modes')]
for key,label in [('enabled','启用扩展行为'),('homeostasis_enabled','饥饿 / 口渴 / 疲劳'),
                  ('water_enabled','寻水与饮水停留'),('rest_enabled','疲劳休息'),('groom_enabled','前足清洁动作'),
                  ('learning_enabled','气味偏好学习'),('search_enabled','失去气味后局部搜索'),
                  ('light_enabled','明暗偏好'),('thermal_enabled','温度舒适趋向'),
                  ('wind_enabled','风向引导'),('startle_enabled','复眼扩张惊逃')]:
    FIELDS.append(dict(key=key,label=label,kind='boolean',group='learning' if key=='learning_enabled' else 'senses' if key in ('light_enabled','thermal_enabled','wind_enabled','startle_enabled','search_enabled') else 'modes'))
FIELDS += [dict(key='light_preference',label='明暗偏好',kind='choice',choices={'balanced':'趋向适光，避强光','approach':'趋光','avoid':'趋暗'},group='senses'),
           dict(key='bright_threshold',label='强光阈值',minimum=.45,maximum=1.,step=.05,group='senses'),
           dict(key='preferred_temp_c',label='舒适温度 / °C',minimum=15.,maximum=35.,step=1,group='senses'),
           dict(key='wind_strength',label='风强度 / 代理值',minimum=0.,maximum=1.,step=.05,group='senses'),
           dict(key='wind_direction_deg',label='风吹向 / °，0 为地图右方',minimum=0.,maximum=360.,step=15,group='senses'),
           dict(key='learning_rate',label='气味偏好学习速率',minimum=0.,maximum=1.,step=.05,group='learning')]


@dataclass(frozen=True)
class BehaviorControls:
    enabled: bool=True
    mode: str='auto'
    homeostasis_enabled: bool=True
    water_enabled: bool=True
    rest_enabled: bool=True
    groom_enabled: bool=True
    learning_enabled: bool=True
    search_enabled: bool=True
    light_enabled: bool=True
    light_preference: str='balanced'
    bright_threshold: float=.8
    thermal_enabled: bool=True
    preferred_temp_c: float=25.
    wind_enabled: bool=True
    wind_strength: float=.25
    wind_direction_deg: float=0.
    startle_enabled: bool=True
    learning_rate: float=.15

    def snapshot(self):return asdict(self)

    def updated(self,values):
        if not isinstance(values,dict) or not values:raise ValueError('请提交非空行为设置')
        specs={f['key']:f for f in FIELDS};normalized={}
        for key,value in values.items():
            if key not in specs:raise ValueError('未知行为设置：'+key)
            f=specs[key]
            if f.get('kind')=='boolean':
                if type(value) is not bool:raise ValueError(f['label']+'须为开关')
            elif f.get('kind')=='choice':
                if not isinstance(value,str) or value not in f['choices']:raise ValueError(f['label']+'选项无效')
            else:
                if type(value) not in (int,float) or not math.isfinite(value) or not f['minimum']<=value<=f['maximum']:
                    raise ValueError(f['label']+'超出范围或不是有限数值')
                value=float(value)
            normalized[key]=value
        return replace(self,**normalized)


def validate_needs(values):
    if not isinstance(values,dict) or not values or set(values)-{'hunger','thirst','fatigue','dust'}:
        raise ValueError('需要状态只能包含饥饿、口渴、疲劳、清洁负荷')
    if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in values.values()):
        raise ValueError('需要状态必须是 0–1 的有限数值')
    return {k:float(v) for k,v in values.items()}


def wind_sample(settings,yaw):
    angle=math.radians(settings['wind_direction_deg']);strength=settings['wind_strength']
    error=math.atan2(math.sin(angle+math.pi-yaw),math.cos(angle+math.pi-yaw))
    return dict(strength=strength,direction_deg=settings['wind_direction_deg'],
                toward_xy=[strength*math.cos(angle),strength*math.sin(angle)],upwind_error_rad=error)


class AdaptiveBehavior:
    def __init__(self,seed=0):
        self.needs=dict(hunger=.65,thirst=.4,fatigue=.05,dust=.1)
        self.values=np.array([.5,.5],dtype=float)
        self.eligibility=np.zeros(2)
        self.previous_odor=0.;self.lost_remaining=0.;self.odor_age=100.
        self.rest_remaining=0.;self.groom_remaining=0.;self.startle_remaining=0.;self.startle_cooldown=0.
        self.search_sign=1 if seed%2 else -1
        self.time=0.;self.previous_position=None;self.state='initializing';self.reason='等待感觉输入'
        self.last_reward=0.;self.learning_updates=0;self.groom_elapsed=0.;self.last_output={}

    def set_needs(self,values):self.needs.update(validate_needs(values))

    def reinforce(self,reward,settings):
        if type(reward) not in (int,float) or not math.isfinite(reward) or not -1<=reward<=1:raise ValueError('奖励须为 -1–1')
        if not settings['enabled'] or not settings['learning_enabled']:return False
        if self.eligibility.max()<.02:return False
        # A bounded value estimator with a decaying odor eligibility trace.
        prediction=float(np.dot(self.values,self.eligibility)/max(1.,self.eligibility.sum()))
        self.values=np.clip(self.values+settings['learning_rate']*(reward-prediction)*self.eligibility,-1,1)
        self.last_reward=float(reward);self.learning_updates+=1
        return True

    def clear_memory(self):
        self.values[:]=.5;self.eligibility[:]=0;self.last_reward=0.;self.learning_updates=0

    def snapshot(self,enabled=True):
        return dict(enabled=enabled,active_model_seconds=self.time,state=self.state,reason=self.reason,needs=dict(self.needs),
                    odor_values=dict(sugar=float(self.values[0]),fruit=float(self.values[1])),
                    odor_trace=self.eligibility.tolist(),last_reward=self.last_reward,learning_updates=self.learning_updates,
                    motor_pattern='foreleg_groom' if self.state=='groom' else 'walking_cpg',
                    interpretation='Engineering needs and external odor-value memory, not LIF plasticity or metabolic physiology')

    def step(self,sense,config,dt,policy):
        p=config.behavior_controls;c=sense.get('channels',{});mode=p['mode'];self.time+=dt
        self.startle_cooldown=max(0.,self.startle_cooldown-dt)
        pos=np.asarray(sense['position_mm'][:2]);moved=0. if self.previous_position is None else min(2.,float(np.linalg.norm(pos-self.previous_position)))
        self.previous_position=pos.copy()
        odor=np.array([np.mean(c.get('sugar_odor',[0,0])),np.mean(c.get('fruit_odor',[0,0]))]) if config.odor_enabled else np.zeros(2)
        self.eligibility=self.eligibility*math.exp(-dt/1.5)+odor*(1-math.exp(-dt/1.5))
        hungry=self.needs['hunger'] if p['homeostasis_enabled'] else .65
        thirsty=self.needs['thirst'] if p['homeostasis_enabled'] else .4
        if p['homeostasis_enabled']:
            self.needs['hunger']=min(1.,hungry+.004*dt)
            self.needs['thirst']=min(1.,thirsty+.005*dt)
            self.needs['fatigue']=min(1.,self.needs['fatigue']+.005*dt+.004*moved)
            self.needs['dust']=min(1.,self.needs['dust']+.007*dt+.003*moved)
        threat=bool(config.contact_enabled and sense.get('solid_contacts')) or c.get('bitter',0)>.05 or max(c.get('temperature_c',[25,25]))>34.6
        visual=sense.get('vision') or {};loom=max(visual.get('expansion',[0,0]))
        if p['startle_enabled'] and config.vision_enabled and loom>1.2 and max(visual.get('dark_occupancy',[0,0]))>.18 and self.startle_cooldown<=0:
            self.startle_remaining=.24;self.startle_cooldown=1.5
        modified={**sense,'channels':{**c,'light':[.35,.35],'humidity':[0.,0.],'water_contact':False}}
        willing_to_feed=(mode not in ('rest','groom','explore','water','light')) and hungry>.12 and (mode=='forage' or not p['water_enabled'] or thirsty<=max(.55,hungry+.1))
        modified['taste']=sense['taste'] and willing_to_feed and not threat and self.startle_remaining<=0
        if not willing_to_feed or threat or self.startle_remaining>0:policy.feed_remaining=0.
        weights=np.clip(1+self.values*.8,.2,1.8) if p['learning_enabled'] else np.ones(2)
        sugars=np.array(c.get('sugar_odor',[0,0]));fruits=np.array(c.get('fruit_odor',[0,0]))
        if config.odor_enabled:
            modified['odor']=np.clip((sugars*weights[0]+fruits*weights[1])*(.15+.85*hungry),0,1).tolist()
        base=policy._step_legacy(modified,config,dt)
        if p['learning_enabled']:
            reward=-float(c.get('bitter',0)) if c.get('bitter',0)>.05 else float(c.get('sweetness',0)) if modified['taste'] else None
            if reward is not None:
                # dt-normalized update rate, independent of wall time.
                learning={**p,'learning_rate':p['learning_rate']*dt}
                self.reinforce(reward,learning)
        def emit(result,reason):
            # Taste afferents remain driven by real foot contact even after satiety
            # or while a different motor program is selected. Feeding is separate.
            result['sugar_rate_hz']=200.*c.get('sweetness',0.) if sense['taste'] else 0.
            self.state=result['state'];self.reason=reason
            if self.state!='groom':self.groom_elapsed=0.
            result.update(behavior=self.snapshot(),reason=reason,visual_turn=result.get('visual_turn',0.),sensory_turn=result.get('sensory_turn',0.))
            self.last_output=result;return result
        def command(state,drive,reason,sugar=0.):return emit(policy.command(state,drive,sugar),reason)
        if base['state'] in ('hazard_escape','backup_reflex','avoid_reflex'):
            self.rest_remaining=self.groom_remaining=0.
            return emit(base,'危险、实体接触或复眼避障优先')
        if self.startle_remaining>0:
            self.startle_remaining=max(0.,self.startle_remaining-dt)
            return command('visual_startle',[-.9,-.65],'真实复眼中的视野占据快速增加：短时后退转向')
        if base['state']=='visual_avoid':return emit(base,'实际复眼避障优先于目标选择')
        if base['state']=='food_dwell':
            if p['homeostasis_enabled'] and modified['taste']:self.needs['hunger']=max(0.,self.needs['hunger']-.16*dt*c.get('sweetness',0.))
            return emit(base,'足部甜味接触，按饥饿程度停留；气味与甜味配对更新偏好')
        wet=bool(c.get('water_contact')) and config.humidity_enabled and p['water_enabled']
        if wet and (mode=='water' or mode=='auto' and thirsty>.2):
            if p['homeostasis_enabled']:self.needs['thirst']=max(0.,self.needs['thirst']-.3*dt)
            return command('drink',[0,0],'足部进入水源，工程饮水停留降低口渴代理值')
        if p['rest_enabled'] and (mode=='rest' or mode=='auto' and p['homeostasis_enabled'] and self.needs['fatigue']>.8):self.rest_remaining=max(self.rest_remaining,3.)
        if self.rest_remaining>0 and p['rest_enabled']:
            self.rest_remaining=max(0.,self.rest_remaining-dt)
            if p['homeostasis_enabled']:self.needs['fatigue']=max(0.,self.needs['fatigue']-.22*dt)
            return command('rest',[0,0],'主动休息或疲劳达到阈值；暂停行走，脑计算继续')
        can_groom=p['groom_enabled'] and sense['upright']>.8
        if can_groom and (mode=='groom' or mode=='auto' and p['homeostasis_enabled'] and self.needs['dust']>.7 and hungry<.6 and thirsty<.6):self.groom_remaining=max(self.groom_remaining,1.2)
        if self.groom_remaining>0 and can_groom:
            self.groom_remaining=max(0.,self.groom_remaining-dt);self.groom_elapsed+=dt
            if p['homeostasis_enabled']:self.needs['dust']=max(0.,self.needs['dust']-.4*dt)
            result=policy.command('groom',[0,0]);result['groom_elapsed_s']=self.groom_elapsed
            return emit(result,'右前足抬至头部附近摆扫，其余五足支撑，属于工程动作程序')
        light=np.array(c.get('light',[.35,.35]));temp=np.array(c.get('temperature_c',[25,25]));humidity=np.array(c.get('humidity',[0,0]))
        turn=0.;state='explore';reason='无强目标时持续探索'
        desired=np.asarray(base['desired_drive'],dtype=float)
        speed=float(np.mean(abs(desired)));turn=float((desired[1]-desired[0])/2)
        if mode=='explore':speed=.7;turn=policy.wander*.8
        elif mode=='forage' or mode=='auto' and hungry>=thirsty:
            state='learned_foraging' if p['learning_enabled'] else 'odor_explore';reason='结合实际气味梯度、饥饿和气味偏好选择方向'
        if p['water_enabled'] and config.humidity_enabled and (mode=='water' or mode=='auto' and thirsty>max(.55,hungry+.1)):
            state='seek_water';reason='口渴优先：按左右湿度差寻水';speed=.65;turn=float(3*(humidity[0]-humidity[1])+policy.wander*.2)
        if p['light_enabled'] and config.light_enabled and mode not in ('forage','water','explore'):
            sign=-1. if p['light_preference']=='avoid' or p['light_preference']=='balanced' and max(light)>p['bright_threshold'] else 1.
            light_turn=float(sign*1.5*(light[0]-light[1]));turn+=light_turn
            if mode=='light' or abs(light_turn)>.03:state='avoid_bright' if sign<0 else 'seek_light';reason='明暗代理场的左右差驱动趋光或趋暗'
        if p['thermal_enabled'] and config.thermal_enabled:
            comfort=np.abs(temp-p['preferred_temp_c']);thermal=float(.13*(comfort[1]-comfort[0]));turn+=thermal
            if abs(thermal)>.03 and state not in ('seek_water',):state='thermal_seek';reason='朝左右触角温度更接近舒适值的一侧转向'
        wind=c.get('wind',{})
        if p['wind_enabled'] and wind.get('strength',0)>.01 and config.odor_enabled and odor.max()>.02 and state not in ('seek_water','avoid_bright','thermal_seek') and mode not in ('explore','rest','groom','light'):
            error=wind['upwind_error_rad'];cross=math.sin(error)
            if math.cos(error)<0 and abs(cross)<.05:cross=self.search_sign*.7
            turn+=.28*wind['strength']*cross;state='upwind_track';reason='嗅到气味后结合均匀风向代理输入逆风追踪'
        total=float(odor.max())
        if total>.08:self.odor_age=0.
        else:self.odor_age+=dt
        if self.previous_odor>.08 and (total<.08 or total<self.previous_odor*.55):self.lost_remaining=2.
        elif total>.12 and total>self.previous_odor:self.lost_remaining=0.
        self.previous_odor=total
        if p['search_enabled'] and config.odor_enabled and self.lost_remaining>0 and mode in ('auto','forage') and state not in ('seek_water','avoid_bright','thermal_seek'):
            self.lost_remaining=max(0.,self.lost_remaining-dt);phase=math.sin(self.lost_remaining*math.pi*2)
            speed=.38;turn=.25*self.search_sign*(1 if phase>=0 else -1);state='odor_search';reason='近期气味下降，依据短时气味记忆进行局部摆扫'
        # Preserve repellent avoidance even when an attractive goal is active.
        repel=c.get('repellent_odor',[0,0]);turn+=2*(repel[1]-repel[0])
        if max(repel)>.15:state='hazard_avoid';reason='排斥气味优先修正行走方向'
        limit=min(.48,max(.02,speed-.12));turn=float(np.clip(turn,-limit,limit))
        result=policy.command(state,np.clip([speed-turn,speed+turn],.12,1.2),turn=turn)
        result['sensory_turn']=turn;return emit(result,reason)


def schema():return dict(fields=FIELDS,defaults=BehaviorControls().snapshot(),needs=list(validate_needs(dict(hunger=0,thirst=0,fatigue=0,dust=0))),
                         interpretation='外置工程行为与气味价值学习；不改写全脑 LIF 权重')
