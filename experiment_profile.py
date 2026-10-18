"""Validated experiment conditions; deliberately not a neural/body checkpoint.

Importing this module never imports MuJoCo, torch, the launcher, or a worker.
Host paths, network ports and executable arguments are never read from profiles.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from arena_elements import validate_elements, SENSORY_SWITCHES, MAX_ELEMENTS
from brain_controls import BrainControls
from behavior_model import BehaviorControls, validate_needs
from neural_intervention import validate_protocol, resolve_targets
from mb_learning import MBLearningControls,validate_controls as validate_mb_controls,validate_teaching
from mb_learning_catalog import preview_selection,validate_local_memory

SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parent
CODE_FILES = (
    'habitat.py','habitat_world.py','brain_worker.py','brain_controls.py',
    'behavior_model.py','visual_navigation.py','arena_elements.py',
    'arena_appearance.py','grooming_motor.py','transmitter_modulation.py',
    'brain-model/code/benchmark.py','brain-model/code/run_pytorch.py',
    'experiment_profile.py','play_fly.py','habitat_visuals.py',
    'brain_client.py','neuron_annotations.py','neural_intervention.py','neural_intervention_runtime.py',
    'mb_learning.py','mb_learning_runtime.py','mb_learning_catalog.py',
    'mb_observer.py',
)
ENV_KEYS = {'half_size','food','food_radius','odor_sigma','spawn','odor_enabled',
            'contact_enabled','taste_enabled','vision_enabled','proximity_enabled',
            'multisensory_enabled',*SENSORY_SWITCHES,'elements','behavior_controls','obstacles'}
SWITCHES = {'odor_enabled','contact_enabled','taste_enabled','vision_enabled',
            'proximity_enabled','multisensory_enabled',*SENSORY_SWITCHES}
DEFAULT_BEHAVIOR = dict(needs=dict(hunger=.65,thirst=.4,fatigue=.05,dust=.1),
                        odor_values=dict(sugar=.5,fruit=.5))


def code_fingerprints():
    """Hash fixed local source names only; never follow names supplied by a bundle."""
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in CODE_FILES}


def _finite_json(value,depth=0):
    if depth>32:raise ValueError('配置嵌套过深')
    if isinstance(value,float) and not math.isfinite(value):raise ValueError('配置不能包含 NaN 或无穷数值')
    if type(value) is int and abs(value)>10**300:raise ValueError('配置整数超出支持范围')
    if isinstance(value,dict):
        if any(not isinstance(k,str) for k in value):raise ValueError('配置字段名须为字符串')
        for v in value.values():_finite_json(v,depth+1)
    elif isinstance(value,list):
        for v in value:_finite_json(v,depth+1)


def _number(value, low, high, label):
    if type(value) not in (int,float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError(label+'须为有效范围内的有限数值')
    return float(value)


def _object(value, allowed, label, required=None):
    if not isinstance(value,dict) or set(value)-set(allowed) or (set(required or ())-set(value)):
        raise ValueError(label+'包含缺失或不支持的字段')
    return value


def validate_behavior_state(value):
    _object(value, {'needs','odor_values'}, '外置行为状态', {'needs','odor_values'})
    needs=validate_needs(value['needs'])
    if set(needs)!=set(DEFAULT_BEHAVIOR['needs']):raise ValueError('需要状态必须包含四个字段')
    odor=_object(value['odor_values'], {'sugar','fruit'}, '气味偏好', {'sugar','fruit'})
    return dict(needs=needs,odor_values={k:_number(v,-1,1,'气味偏好') for k,v in odor.items()})


def validate_environment(value):
    env=deepcopy(_object(value,ENV_KEYS,'场地配置',ENV_KEYS))
    # The current ground renderer/world is built for this arena size.
    if type(env['half_size']) not in (int,float) or env['half_size']!=25:
        raise ValueError('当前仅支持半边长 25 mm 的场地')
    for key in SWITCHES:
        if type(env[key]) is not bool:raise ValueError(key+'须为布尔开关')
    env['food_radius']=_number(env['food_radius'],.8,4,'食物半径')
    env['odor_sigma']=_number(env['odor_sigma'],.1,100,'气味范围')
    for key in ('food','spawn'):
        point=env[key]
        if not isinstance(point,list) or len(point)!=2:raise ValueError(key+'须为二维坐标')
        env[key]=[_number(x,-23,23,key) for x in point]
    obstacles=env['obstacles']
    if not isinstance(obstacles,list) or len(obstacles)>24:raise ValueError('实体障碍列表无效')
    for ob in obstacles:
        if not isinstance(ob,dict) or ob.get('type') not in ('circle','box'):raise ValueError('实体障碍类型无效')
        keys={'type','x','y','radius'} if ob['type']=='circle' else {'type','x','y','hx','hy'}
        _object(ob,keys,'实体障碍',keys)
        for k in keys-{'type'}:_number(ob[k],-24 if k in ('x','y') else .1,24,'实体障碍')
        hx=ob.get('radius',ob.get('hx'));hy=ob.get('radius',ob.get('hy'))
        if abs(ob['x'])+hx>25 or abs(ob['y'])+hy>25:raise ValueError('实体障碍越过围栏')
        x,y=env['spawn']
        if ((math.hypot(x-ob['x'],y-ob['y'])<ob['radius']+1.5) if ob['type']=='circle'
            else (abs(x-ob['x'])<ob['hx']+1.5 and abs(y-ob['y'])<ob['hy']+1.5)):
            raise ValueError('出生位置与实体障碍相交')
    elements=env['elements']
    if not isinstance(elements,list):raise ValueError('场地元素须为列表')
    for el in elements:
        _object(el,{'id','kind','label','x','y','radius','strength','slot'},'场地元素',
                {'id','kind','label','x','y','radius','strength','slot'})
        if not isinstance(el['id'],str) or not el['id'] or len(el['id'])>80:raise ValueError('元素 ID 无效')
        if type(el['slot']) is not int or not 0<=el['slot']<MAX_ELEMENTS:raise ValueError('元素槽位无效')
    validate_elements(elements,25,obstacles)
    primary=next(e for e in elements if e['id']=='food')
    if env['food']!=[primary['x'],primary['y']] or env['food_radius']!=primary['radius']:
        raise ValueError('主要食物坐标或半径与场地元素不一致')
    env['behavior_controls']=BehaviorControls().updated(env['behavior_controls']).snapshot()
    return env


def validate_command(message, environment):
    """Accept only settings that the body thread actually knows how to apply."""
    if not isinstance(message,dict):raise ValueError('设置事件必须为对象')
    kind=message.get('type')
    if kind=='mb_learning_settings':
        _object(message,{'type','values'},kind,{'type','values'})
        MBLearningControls().updated(message['values'])
    elif kind=='mb_learning_reward':
        _object(message,{'type','reward'},kind,{'type','reward'});validate_teaching(message['reward'])
    elif kind=='mb_learning_reset':
        _object(message,{'type'},kind,{'type'})
    elif kind=='neural_intervention':
        _object(message,{'type','protocol'},kind,{'type','protocol'})
        protocol=validate_protocol(message['protocol'])
        if protocol is not None:
            from neuron_annotations import annotations
            resolve_targets(protocol,annotations().rows)
        return dict(type=kind,protocol=protocol)
    elif kind=='brain_settings':
        _object(message,{'type','values'},kind,{'type','values'})
        BrainControls().updated(message['values'])
    elif kind=='behavior_settings':
        _object(message,{'type','values'},kind,{'type','values'})
        BehaviorControls().updated(message['values'])
    elif kind=='behavior_needs':
        _object(message,{'type','values'},kind,{'type','values'});validate_needs(message['values'])
    elif kind=='behavior_memory_reset':_object(message,{'type'},kind,{'type'})
    elif kind=='behavior_reward':
        _object(message,{'type','reward'},kind,{'type','reward'});_number(message['reward'],-1,1,'奖励')
    elif kind=='sensory_settings':
        _object(message,{'type','values'},kind,{'type','values'})
        values=message['values']
        if not isinstance(values,dict) or not values or set(values)-set(SENSORY_SWITCHES) or any(type(x) is not bool for x in values.values()):
            raise ValueError('感觉设置无效')
    elif kind in ('odor','contact','taste','vision'):
        _object(message,{'type','enabled'},kind,{'type','enabled'})
        if type(message['enabled']) is not bool:raise ValueError('感觉开关无效')
    elif kind=='arena_edit':
        _object(message,{'type','elements','arena_revision','request'},kind,{'type','elements','arena_revision'})
        trial=deepcopy(environment);trial['elements']=deepcopy(message['elements'])
        primary=next((e for e in trial['elements'] if isinstance(e,dict) and e.get('id')=='food'),None)
        if primary is None:raise ValueError('重放场地缺少主要食物')
        trial['food']=[primary['x'],primary['y']];trial['food_radius']=primary['radius']
        validate_environment(trial)
        if type(message['arena_revision']) is not int or message['arena_revision']<0:raise ValueError('场地修订号无效')
        # Request is explanatory only; never execute nested input from the file.
        return dict(type=kind,elements=deepcopy(trial['elements']),arena_revision=message['arena_revision'],request={'action':'replay'})
    else:raise ValueError('不支持的设置事件：'+str(kind))
    return deepcopy(message)


def validate_profile(profile):
    _finite_json(profile)
    keys={'schema_version','environment','brain_controls','behavior_initial','seed','seconds',
          'no_brain','disconnect_brain_output','events','provenance','limitations','neural_intervention',
          'mb_learning_controls','learning_memory','mb_learning_reward'}
    p=deepcopy(_object(profile,keys,'复现配置',keys-{'provenance','limitations','neural_intervention',
        'mb_learning_controls','learning_memory','mb_learning_reward'}))
    if type(p['schema_version']) is not int or p['schema_version']!=SCHEMA_VERSION:raise ValueError('不支持的复现配置版本')
    p['environment']=validate_environment(p['environment'])
    p['brain_controls']=BrainControls().updated(p['brain_controls']).snapshot()
    p['neural_intervention']=validate_protocol(p.get('neural_intervention'))
    if p['neural_intervention'] is not None:
        from neuron_annotations import annotations
        resolve_targets(p['neural_intervention'],annotations().rows)
    p['behavior_initial']=validate_behavior_state(p['behavior_initial'])
    p['mb_learning_controls']=validate_mb_controls(p.get('mb_learning_controls',MBLearningControls().snapshot()))
    p['mb_learning_reward']=validate_teaching(p.get('mb_learning_reward',0.))
    if p['mb_learning_controls']['enabled']:preview_selection(p['mb_learning_controls'])
    p['learning_memory']=validate_local_memory(p.get('learning_memory'),p['mb_learning_controls'])
    if type(p['seed']) is not int or not 0<=p['seed']<=2**32-1:raise ValueError('随机种子须为 0–2^32-1 整数')
    duration=_number(p['seconds'],0,3600,'模型时长')
    if duration and duration<.02:raise ValueError('模型时长须为 0 或至少 0.02 秒')
    for k in ('no_brain','disconnect_brain_output'):
        if type(p[k]) is not bool:raise ValueError(k+'须为布尔开关')
    if p['no_brain'] and p['neural_intervention'] is not None:
        raise ValueError('神经干预需要启用脑模型')
    if p['no_brain'] and (p['mb_learning_controls']['enabled'] or p['learning_memory'] is not None):
        raise ValueError('蘑菇体局部学习和突触记忆需要脑模型')
    if not isinstance(p['events'],list) or len(p['events'])>100000:raise ValueError('设置事件列表过大或无效')
    previous=-1;mb_state=dict(p['mb_learning_controls'])
    for entry in p['events']:
        _object(entry,{'time_s','command'},'设置事件',{'time_s','command'})
        stamp=_number(entry['time_s'],0,1e9,'事件模型时间')
        if stamp<previous:raise ValueError('设置事件时间必须按顺序排列')
        if duration and stamp>duration+1e-8:raise ValueError('设置事件晚于实验配置的结束时间')
        previous=stamp;entry['command']=validate_command(entry['command'],p['environment'])
        if p['no_brain'] and entry['command']['type']=='neural_intervention' and entry['command']['protocol'] is not None:
            raise ValueError('无脑实验不能重放神经干预')
        command=entry['command']
        if command['type'].startswith('mb_learning_'):
            if p['no_brain']:raise ValueError('无脑实验不能重放蘑菇体学习命令')
            if command['type']=='mb_learning_settings':
                mb_state=validate_mb_controls(command['values'],mb_state);preview_selection(mb_state)
    provenance=p.get('provenance')
    if provenance is not None:
        if not isinstance(provenance,dict):raise ValueError('来源说明须为对象')
        source=provenance.get('source_project')
        if source is not None and not isinstance(source,dict):raise ValueError('来源模型版本无效')
        from project_info import project_info
        local=project_info();warnings=[]
        for key in ('version','data_release','brain_source_commit','annotation_commit','flygym_version','flygym_source_commit'):
            recorded=(source or {}).get(key)
            if recorded is None:warnings.append('源记录未提供 '+key+'，兼容性未知')
            elif recorded!=local.get(key):raise ValueError('实验条件与本地模型版本不匹配：'+key)
        if not (source or {}).get('source_commit'):warnings.append('源程序 Git 提交 SHA 未记录；关键源码摘要只覆盖列出的文件')
        hashes=provenance.get('source_code_sha256')
        if hashes is None:
            warnings.append('旧记录未提供关键源码 SHA256，程序条件兼容性未知')
        else:
            if not isinstance(hashes,dict) or not hashes or set(hashes)-set(CODE_FILES):
                raise ValueError('关键源码摘要必须为固定白名单文件组成的非空对象')
            for name,digest in hashes.items():
                if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',digest):
                    raise ValueError('关键源码摘要格式无效：'+name)
            # Compare through our own fixed names, never an archive-provided path.
            for name in CODE_FILES:
                digest=hashes.get(name)
                if digest is None:
                    warnings.append('源记录未提供关键源码摘要：'+name)
                    continue
                try:local_digest=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                except OSError as error:raise ValueError('本地关键源码无法读取：'+name) from error
                if digest.lower()!=local_digest:raise ValueError('实验关键源码与本地不匹配：'+name)
        dependencies=provenance.get('source_dependencies')
        if dependencies is not None:
            if not isinstance(dependencies,dict):raise ValueError('依赖版本说明无效')
            from importlib import metadata
            for package in ('flygym','mujoco','numpy'):
                recorded=dependencies.get(package)
                if recorded is None:continue
                try:installed=metadata.version(package)
                except metadata.PackageNotFoundError:installed=None
                if installed is None:warnings.append('本地 '+package+' 版本无法确定')
                elif recorded!=installed:raise ValueError('实验依赖版本不匹配：'+package)
        provenance['compatibility_warnings']=warnings
    return p


def _read(path):
    if path.stat().st_size>32*1024*1024:raise ValueError('配置文件过大')
    return json.loads(path.read_text(encoding='utf-8'),parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限 JSON 数值：'+value)))


def _read_report(directory):
    """A valid saved error report is the fallback when report.json was unwritable."""
    for name in ('report.json','error.json'):
        path=directory/name
        if path.is_file():
            value=_read(path)
            if value:return value
    return {}


def profile_from_run(directory, mode='initial', replay_events=True):
    try:return _profile_from_run(directory,mode,replay_events)
    except (KeyError,TypeError,StopIteration,AttributeError,OverflowError) as error:
        raise ValueError('实验记录缺少字段或结构无效：'+str(error)) from error


def _profile_from_run(directory, mode='initial', replay_events=True):
    """Rebuild experiment conditions from a saved run, never resume brain state."""
    if mode not in ('initial','final') or type(replay_events) is not bool:raise ValueError('复现选项无效')
    directory=Path(directory)
    config=_read(directory/'config.json')
    if type(config.get('schema_version',1)) is not int or config.get('schema_version',1)!=1:raise ValueError('不支持的实验配置版本')
    args=config['arguments'];environment=config['environment'];brain=config['brain_controls']
    state=config.get('behavior_initial',DEFAULT_BEHAVIOR)
    intervention=config.get('neural_intervention')
    mb_controls=config.get('mb_learning_controls',MBLearningControls().snapshot())
    memory=config.get('learning_memory');mb_reward=config.get('mb_learning_reward',0.)
    if mode=='final':
        report=_read_report(directory)
        if type(report.get('schema_version',1)) is not int or report.get('schema_version',1)!=1:raise ValueError('不支持的实验报告版本')
        environment=report['arena_final'];brain=report['brain_controls_final']
        behavior=report.get('behavior_final')
        if not behavior:raise ValueError('该记录尚无可用的最终外置行为状态')
        state={k:behavior[k] for k in ('needs','odor_values')}
        intervention=report.get('neural_intervention_final')
        mb_controls=report.get('mb_learning_controls_final',MBLearningControls().snapshot())
        mb_reward=report.get('mb_learning_reward_final',0.)
        memory=report.get('learning_memory')
        if memory is None and (directory/'learning_memory.json').is_file():memory=_read(directory/'learning_memory.json')
        if (report.get('mb_learning_actual') or {}).get('initialized') and memory is None:
            raise ValueError('缺少已初始化蘑菇体的局部突触记忆，不能假装恢复为已学习条件')
    events=[]
    if mode=='initial' and replay_events:
        path=directory/'events.jsonl'
        if not path.is_file():raise ValueError('缺少设置事件文件；可明确关闭事件重放后导入')
        if path.stat().st_size>64*1024*1024:raise ValueError('事件文件过大')
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            entry=json.loads(line)
            kind=entry.get('type')
            if kind=='command':
                message=entry['command']
                if message.get('type') in ('pause','stop','restart','reproduce'):continue
                events.append(dict(time_s=entry['time_s'],command=message))
            elif kind not in ('state','first_food_contact','solid_contact','arena_edit','brain_settings',
                              'behavior_settings','behavior_needs','behavior_memory_reset','behavior_reward','replay_source',
                              'neural_intervention','neural_intervention_phase'):
                if kind not in ('mb_learning_changed','mb_learning_rejected'):
                    raise ValueError('未知的事件记录：'+str(kind))
    seconds=args['seconds'];duration_origin='original configured duration'
    if mode=='initial' and seconds==0:
        duration=_read_report(directory).get('body_model_seconds')
        if type(duration) in (int,float) and math.isfinite(duration) and .02<=duration<=3600:
            seconds=duration;duration_origin='completed recorded model time (source was continuous)'
        else:duration_origin='source was continuous; recorded duration unavailable or outside 0.02–3600 s; kept continuous, stop manually'
    elif seconds==0:duration_origin='continuous experiment; stop manually'
    return validate_profile(dict(schema_version=1,environment=environment,brain_controls=brain,
        behavior_initial=state,neural_intervention=intervention,mb_learning_controls=mb_controls,
        learning_memory=memory,mb_learning_reward=mb_reward,seed=args['seed'],seconds=seconds,no_brain=args['no_brain'],
        disconnect_brain_output=args.get('disconnect_brain_output',False),events=events,
        provenance=dict(mode=mode,source_project=config.get('project'),replay_events=bool(mode=='initial' and replay_events),
                        source_dependencies=config.get('dependencies'),source_code_sha256=config.get('code_sha256'),
                        duration_origin=duration_origin),
        limitations='重建条件与模型时间设置事件；外置气味偏好不是全脑学习；不恢复脑/身体/随机生成器中间态，不保证跨硬件逐神经元完全一致。seconds=0 时须手动结束保存；原连续实验超过 3600 秒时仍保持连续，不扩展定时时长上限。'))
