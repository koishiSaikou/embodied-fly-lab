"""Editable floor cues and transparent scalar sensory fields (not fluid/thermal physics)."""
from copy import deepcopy
import math
import threading

MAX_ELEMENTS=24
KINDS={
    'sugar':dict(label='糖水食物',color='#efb76b',rgb=[.92,.57,.16],sugar=1.,fruit=0.,sweetness=1.),
    'fruit':dict(label='果味食物',color='#ed702c',rgb=[.93,.44,.17],sugar=.25,fruit=1.,sweetness=.6),
    'water':dict(label='水源',color='#65b9e9',rgb=[.18,.56,.88],humidity=1.,wet=True),
    'danger':dict(label='危险区',color='#ef7974',rgb=[.82,.16,.12],repellent=1.,heat=12.,bitter=1.),
    'light':dict(label='亮区',color='#f4e89a',rgb=[.98,.94,.55],light=.65),
    'shade':dict(label='阴影区',color='#607791',rgb=[.12,.19,.28],light=-.3),
    'cool':dict(label='凉区 / 冰块',color='#a3def5',rgb=[.53,.82,.94],heat=-10.,humidity=.15),
    'neutral':dict(label='中性对照区',color='#929c9e',rgb=[.5,.52,.53]),
}
SENSORY_SWITCHES=('light_enabled','humidity_enabled','thermal_enabled','bitter_enabled')


def initial_elements(food,radius,rich=False):
    result=[dict(id='food',kind='sugar',label='主要食物',x=float(food[0]),y=float(food[1]),radius=radius,strength=1.,slot=0),
            dict(id='control',kind='neutral',label='中性对照',x=-13.,y=14.,radius=2.5,strength=1.,slot=1)]
    if rich:
        for slot,(kind,x,y,radius,label) in enumerate([
            ('fruit',-15.,6.,2.5,'果味食物'),('water',-13.,-1.,2.5,'水源'),
            ('danger',14.,-16.,2.5,'危险区'),('light',0.,16.,3.,'亮区'),('shade',17.,0.,2.5,'阴影区')],2):
            result.append(dict(id='zone_'+str(slot),kind=kind,label=label,x=x,y=y,radius=radius,strength=1.,slot=slot))
    return result


def geom_name(slot):
    return 'food_zone' if slot==0 else 'sham_zone' if slot==1 else 'arena_element_'+str(slot)


def validate_elements(elements,half_size,obstacles):
    if not isinstance(elements,list) or not 1<=len(elements)<=MAX_ELEMENTS:
        raise ValueError(f'场地元素须为 1–{MAX_ELEMENTS} 个')
    ids=set();slots=set()
    for element in elements:
        if element['kind'] not in KINDS:raise ValueError('不支持的场地元素')
        if element['id'] in ids or element['slot'] in slots:raise ValueError('重复元素编号')
        ids.add(element['id']);slots.add(element['slot'])
        for key,low,high in [('x',-half_size,half_size),('y',-half_size,half_size),('radius',.8,4.),('strength',0.,1.)]:
            value=element[key]
            if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
                raise ValueError(f'{key} 必须是 {low}–{high} 范围内的有限数值')
        if max(abs(element['x']),abs(element['y']))+element['radius']>half_size-1:
            raise ValueError('元素需完整放在围栏内，离边界至少 1 mm')
        if not isinstance(element['label'],str) or not 1<=len(element['label'])<=24 or not element['label'].isprintable():
            raise ValueError('名称须为 1–24 个可显示字符')
        x,y,r=element['x'],element['y'],element['radius']
        for obstacle in obstacles:
            if obstacle['type']=='circle':distance=math.hypot(x-obstacle['x'],y-obstacle['y'])-obstacle['radius']
            else:
                dx=max(abs(x-obstacle['x'])-obstacle['hx'],0)
                dy=max(abs(y-obstacle['y'])-obstacle['hy'],0)
                distance=math.hypot(dx,dy)
            if distance<r+.3:raise ValueError('元素不能覆盖实体障碍，请选择空地')
    if not any(e['id']=='food' and e['slot']==0 and e['kind'] in ('sugar','fruit') for e in elements):
        raise ValueError('必须保留主要食物；可移动、缩放或把强度设为 0')
    for i,a in enumerate(elements):
        for b in elements[i+1:]:
            if a['kind'] in ('light','shade') or b['kind'] in ('light','shade'):continue
            if math.hypot(a['x']-b['x'],a['y']-b['y'])<a['radius']+b['radius']+.3:
                raise ValueError('食物、水源、危险区、凉区和对照区不能互相覆盖；亮区和阴影可叠加')
    return elements


class ArenaEditor:
    """Reserve validated layouts before queueing, so concurrent additions cannot reuse a slot."""
    def __init__(self,elements,half_size,obstacles):
        self.projected=deepcopy(validate_elements(elements,half_size,obstacles))
        self.half_size,self.obstacles=half_size,obstacles
        self.lock=threading.Lock()
        self.revision=0

    def prepare(self,message):
        with self.lock:
            elements=deepcopy(self.projected)
            action=message.get('action');values=message.get('values',{})
            if not isinstance(values,dict):raise ValueError('元素参数必须是对象')
            if set(values)-{'kind','x','y','radius','strength','label'}:raise ValueError('不支持的元素参数')
            if action=='add':
                used={e['slot'] for e in elements}
                slot=next((i for i in range(1,MAX_ELEMENTS) if i not in used),None)
                if slot is None:raise ValueError('元素已达到上限')
                kind=values.get('kind')
                if kind not in KINDS:raise ValueError('请选择元素类型')
                item=dict(id='zone_'+str(self.revision+1)+'_'+str(slot),slot=slot,kind=kind,
                          label=KINDS[kind]['label'],x=0.,y=0.,radius=2.,strength=1.)
                item.update(values);elements.append(item)
            elif action in ('update','delete'):
                item=next((e for e in elements if e['id']==message.get('id')),None)
                if item is None:raise ValueError('元素不存在，可能已被删除')
                if action=='delete':
                    if item['id']=='food':raise ValueError('主要食物不能删除，可把强度设为 0')
                    elements.remove(item)
                else:item.update(values)
            else:raise ValueError('不支持的场地操作')
            validate_elements(elements,self.half_size,self.obstacles)
            self.revision+=1
            self.projected=deepcopy(elements)
            return dict(type='arena_edit',elements=elements,arena_revision=self.revision,request=deepcopy(message))


def sample_fields(elements,xy,feet,food_sigma=13.):
    """All fields are sampled at actual body positions; no target bearing is supplied."""
    import numpy as np
    xy=np.asarray(xy,dtype=float);feet=np.asarray(feet,dtype=float)
    channels={key:np.zeros(len(xy)) for key in ('sugar_odor','fruit_odor','repellent_odor','humidity')}
    light=np.full(len(xy),.35);temperature=np.full(len(xy),25.)
    sweetness=bitter=0.;wet=False;contacts=[];distances=[]
    for element in elements:
        kind=KINDS[element['kind']];center=np.array([element['x'],element['y']]);strength=element['strength']
        d2=np.sum((xy-center)**2,axis=-1)
        sigma=food_sigma if element['id']=='food' else max(2.,element['radius']*2.2)
        odor=np.exp(-d2/(2*sigma**2))*strength
        local=np.exp(-d2/(2*max(1.,element['radius']*.9)**2))*strength
        for key,profile in [('sugar_odor','sugar'),('fruit_odor','fruit'),('repellent_odor','repellent')]:
            channels[key]+=odor*kind.get(profile,0.)
        channels['humidity']+=np.exp(-d2/(2*max(2.,element['radius']*1.6)**2))*strength*kind.get('humidity',0.)
        light+=local*kind.get('light',0.);temperature+=local*kind.get('heat',0.)
        touching=bool(np.any((np.linalg.norm(feet[:,:2]-center,axis=1)<=element['radius'])&(feet[:,2]<.3)))
        if touching:
            contacts.append(element['id'])
            sweetness=max(sweetness,strength*kind.get('sweetness',0.))
            bitter=max(bitter,strength*kind.get('bitter',0.))
            wet|=bool(kind.get('wet') and strength>0)
        if kind.get('sweetness') and strength>0:distances.append(float(np.linalg.norm(xy.mean(axis=0)-center)))
    return dict(**{key:np.clip(value,0,1).tolist() for key,value in channels.items()},
                light=np.clip(light,0,1).tolist(),temperature_c=temperature.tolist(),
                sweetness=float(sweetness),bitter=float(bitter),water_contact=wet,contacts=contacts,
                nearest_food_antenna_distance_mm=min(distances) if distances else None)


def schema():
    return dict(kinds=KINDS,max_elements=MAX_ELEMENTS,sensory_switches=list(SENSORY_SWITCHES),
                interpretation='Recognizable non-colliding props and scalar proxy fields; not fluid/thermal/receptor physics')
