"""Validated brain inputs, motor readout and temporary transmitter-group gains."""
from dataclasses import asdict, dataclass, replace
import math
from transmitter_modulation import fields as transmitter_fields, TRANSMITTERS

FIELDS = [
    dict(key='p9_gain_a', label='P9 通道 A 刺激增益', unit='倍', minimum=0, maximum=3, step=.1,
         help='乘在策略给通道 A 的刺激频率上；0 表示去掉该通道的策略刺激。'),
    dict(key='p9_gain_b', label='P9 通道 B 刺激增益', unit='倍', minimum=0, maximum=3, step=.1,
         help='乘在策略给通道 B 的刺激频率上；A/B 沿用原模型通道顺序。'),
    dict(key='p9_bias_hz', label='P9 共同基础刺激', unit='Hz', minimum=0, maximum=100, step=5,
         help='增益计算后，给两个通道各加此频率；增益为 0 时仍会保留这项基础刺激。'),
    dict(key='sugar_gain', label='糖味神经元刺激增益', unit='倍', minimum=0, maximum=3, step=.1,
         help='缩放足部食物接触触发的糖味 GRN 刺激；不凭空生成食物接触。'),
    dict(key='motor_gain', label='脑到身体反馈增益', unit='倍', minimum=0, maximum=1.5, step=.1,
         help='缩放 P9 实际放电读出的运动幅度，最终幅度上限仍为 1.2；不是设定真实行走速度。'),
    dict(key='smoothing_ms', label='运动反馈平滑时间', unit='模型毫秒', minimum=20, maximum=500, step=10,
         help='较大值让输出响应更缓；只平滑 P9 到步态的读出，不修改神经元膜电位。'),
    dict(key='feedback_enabled', label='启用脑到身体运动反馈', kind='boolean',
         help='关闭后运动驱动为 0，脑仍继续计算；接触、重力仍可能使身体移动。'),
] + transmitter_fields()


@dataclass(frozen=True)
class BrainControls:
    p9_gain_a: float = 1.0
    p9_gain_b: float = 1.0
    p9_bias_hz: float = 0.0
    sugar_gain: float = 1.0
    motor_gain: float = 1.0
    smoothing_ms: float = 100.0
    feedback_enabled: bool = True
    nt_acetylcholine: float = 1.0
    nt_glutamate: float = 1.0
    nt_gaba: float = 1.0
    nt_dopamine: float = 1.0
    nt_serotonin: float = 1.0
    nt_octopamine: float = 1.0

    def snapshot(self):
        return asdict(self)

    def transmitter_gains(self):
        return {key: getattr(self,'nt_'+key) for key,_ in TRANSMITTERS}

    def updated(self, changes):
        if not isinstance(changes, dict) or not changes:
            raise ValueError('请提交非空的脑模型调整项')
        specs = {field['key']: field for field in FIELDS}
        normalized = {}
        for key, value in changes.items():
            if key not in specs:
                raise ValueError('未知的脑模型调整项：' + str(key))
            spec = specs[key]
            if spec.get('kind') == 'boolean':
                if type(value) is not bool:
                    raise ValueError(spec['label'] + '必须是开关值')
            elif type(value) not in (int, float) or not math.isfinite(value) or not spec['minimum'] <= value <= spec['maximum']:
                raise ValueError(f"{spec['label']}须为 {spec['minimum']}–{spec['maximum']} 范围内的有限数值")
            else:
                value = float(value)
            normalized[key] = value
        return replace(self, **normalized)

    def encode(self, command):
        rates = [min(1000.0, command['p9_rate_hz'][i] * gain + self.p9_bias_hz)
                 for i, gain in enumerate((self.p9_gain_a, self.p9_gain_b))]
        return {**command, 'policy_p9_rate_hz': list(command['p9_rate_hz']),
                'p9_rate_hz': rates, 'policy_sugar_rate_hz': command['sugar_rate_hz'],
                'sugar_rate_hz': min(1000.0, command['sugar_rate_hz'] * self.sugar_gain)}

    def motor_drive(self, measured_drive, signs):
        gain = self.motor_gain if self.feedback_enabled else 0.0
        return [min(1.2, max(0.0, value * gain)) * sign for value, sign in zip(measured_drive, signs)]


def schema():
    return dict(fields=FIELDS, defaults=BrainControls().snapshot())
