// Live engineering gains. The Python worker remains the source of applied values.
const root=document.getElementById('brainSettingsPanel');
if(root)start().catch(error=>{root.textContent='脑模型调整暂不可用：'+error.message;});

async function start(){
  root.innerHTML=`<div class="panel-head"><div><h2>脑刺激、递质与反馈调整</h2><p class="caption">本轮即时生效 · 改动写入实验记录 · 重开新一轮恢复默认</p></div><a href="#neuralActivityPanel">活动显示与筛选 ↗</a></div>
    <p class="caption">基础项调整感觉输入和运动读出；递质项缩放本轮的有效突触权重。保留 LIF 方程，身体方向仍包含工程策略。</p>
    <form id="bc-form"><fieldset id="bc-all" class="bc-all" disabled><fieldset id="bc-fields" class="bc-groups"><details class="bc-group"><summary>刺激输入 <span>4 项</span></summary><div id="bc-input-fields" class="bc-fields"></div></details><details class="bc-group"><summary>运动反馈 <span>3 项</span></summary><div id="bc-feedback-fields" class="bc-fields"></div></details></fieldset>
    <details class="bc-transmitters"><summary>递质来源突触增益 · 简化干预</summary>
      <p class="caption">按发出信号的神经元的 top_nt 预测分组。0 = 阻断后续输出，1 = 原值，2 = 两倍；保留原连接的正负号。这不是递质浓度、受体作用或奖励学习，已进入延迟队列的信号会继续衰减。</p>
      <fieldset id="bc-nt-fields" class="bc-fields"></fieldset><p id="bc-nt-unknown" class="caption">等待递质分组覆盖…</p>
    </details></fieldset>
    <div class="bc-actions"><button id="bc-apply" type="submit" disabled>应用到本轮实验</button><button id="bc-defaults" type="button" disabled>恢复默认并应用</button></div></form>
    <p id="bc-status" class="bc-status caption" role="status">等待当前实验配置…</p><p id="bc-actual" class="caption bc-actual">等待实际刺激与反馈读出…</p>`;
  const $=id=>document.getElementById('bc-'+id),inputs=new Map();
  let pending=null,dirty=false,running=false,directory=null,ready=false;
  const response=await fetch('/api/brain-controls',{cache:'no-store'});
  if(!response.ok)throw Error('当前服务尚未支持调整，请重启本机 Start-Habitat.cmd 后刷新页面');
  const {fields,defaults}=await response.json();
  if(!fields.some(field=>field.group==='transmitter'))throw Error('当前进程尚未载入递质控制，请结束保存并重启本机 Start-Habitat.cmd 后刷新');
  for(const field of fields){
    const label=document.createElement('label'),title=document.createElement('span'),input=document.createElement('input'),help=document.createElement('small');
    title.textContent=field.label+(field.unit?'（'+field.unit+'）':'');input.id='bc-'+field.key;input.name=field.key;
    input.type=field.kind==='boolean'?'checkbox':'number';
    if(input.type==='number'){input.min=field.minimum;input.max=field.maximum;input.step=field.step;input.required=true;input.inputMode='decimal';}
    help.textContent=field.help;help.id=input.id+'-help';input.setAttribute('aria-describedby',help.id);
    input.addEventListener('input',()=>{dirty=true;$('status').textContent='有未应用的修改。点击“应用到本轮实验”后才会改变仿真。';});
    label.className=input.type==='checkbox'?'bc-toggle':'bc-number';label.append(title,input,help);$(field.group==='transmitter'?'nt-fields':['motor_gain','smoothing_ms','feedback_enabled'].includes(field.key)?'feedback-fields':'input-fields').appendChild(label);inputs.set(field.key,input);
  }
  function fill(values){for(const [key,input]of inputs){if(input.type==='checkbox')input.checked=values[key];else input.value=values[key];}}
  function values(){return Object.fromEntries([...inputs].map(([key,input])=>[key,input.type==='checkbox'?input.checked:input.valueAsNumber]));}
  function matches(a,b){return a&&b&&fields.every(({key})=>a[key]===b[key]);}
  async function apply(){
    if(!running||pending)return;
    const invalid=[...inputs.values()].find(input=>!input.checkValidity());
    if(invalid){const group=invalid.closest('details');if(group)group.open=true;invalid.reportValidity();return;}
    pending=values();$('all').disabled=true;$('apply').disabled=true;$('defaults').disabled=true;
    $('status').textContent='正在提交，等待模型块确认…';
    try{
      const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'brain_settings',values:pending})});
      const result=await response.json();if(!response.ok)throw Error(result.error||response.status);
    }catch(error){pending=null;$('status').textContent='应用失败：'+error.message;$('all').disabled=!running;$('apply').disabled=!running;$('defaults').disabled=!running;}
  }
  $('form').onsubmit=event=>{event.preventDefault();apply();};
  $('defaults').onclick=()=>{fill(defaults);dirty=true;apply();};
  window.BrainControls={update(h,isRunning,metadata,chunk){
    running=isRunning;
    if(directory!==h.directory){directory=h.directory;dirty=false;pending=null;ready=false;}
    const current=h.brain_controls;
    if(!current){$('status').textContent='当前进程仍是旧版，请重启本机 Start-Habitat.cmd 后刷新。';return;}
    if(fields.some(field=>!(field.key in current))){$('status').textContent='当前进程尚未载入递质控制，请重启本机 Start-Habitat.cmd 后刷新。';return;}
    if(pending&&matches(pending,current)){pending=null;dirty=false;$('status').textContent=h.paused?'配置已应用；当前已暂停，继续后使用新参数计算。':'配置已应用，将用于后续模型时间块。';}
    if(!dirty&&!pending)fill(current);
    if(!ready){$('status').textContent='已同步当前配置。修改后点击“应用到本轮实验”。';ready=true;}
    $('all').disabled=!running||!!pending;$('apply').disabled=!running||!!pending;$('defaults').disabled=!running||!!pending;
    const groups=metadata?.transmitter_modulation?.groups||[];
    for(const group of groups){
      const help=document.getElementById('bc-nt_'+group.key+'-help');
      if(help)help.textContent=`预测来源 ${group.neurons.toLocaleString()} 个神经元 · ${group.weighted_edges.toLocaleString()} 条输出连接（原正 ${group.positive_edges.toLocaleString()} / 原负 ${group.negative_edges.toLocaleString()}）。保持原正负号。`;
    }
    const unknown=groups.find(group=>group.key==='__unknown__');
    if(unknown)$('nt-unknown').textContent=`预测未知 / 未匹配：${unknown.neurons.toLocaleString()} 个神经元、${unknown.weighted_edges.toLocaleString()} 条输出连接，固定 1 倍。所有分组基于预测，未按置信度筛除。`;
    if(!running)$('status').textContent='本轮已结束，显示最后使用的参数；在网页重开新一轮后可调整。';
    const stimulus=h.command?.p9_rate_hz||[0,0],sugar=h.command?.sugar_rate_hz||0;
    $('actual').textContent=`最近计算块输入：P9 A ${stimulus[0].toFixed(1)} / B ${stimulus[1].toFixed(1)} Hz · 糖味 ${sugar.toFixed(1)} Hz · 当前配置：`+
      (h.brain_feedback_disconnected?'启动参数已强制切断运动输出':current.feedback_enabled?`运动反馈 ${current.motor_gain.toFixed(1)} 倍 · 平滑 ${current.smoothing_ms} 模型毫秒`:'运动反馈关闭，保留脑计算')+
      (h.paused?'（已暂停；继续后按新配置计算）':'');
    const active=Object.entries(chunk?.transmitter_gains||{}).filter(([,gain])=>gain!==1);
    $('actual').textContent+=' · 最近计算块递质增益：'+(active.length?active.map(([key,gain])=>`${groups.find(group=>group.key===key)?.label||key} ${gain} 倍`).join('、'):'全部 1 倍');
  }};
  // A websocket snapshot can arrive while the schema is loading.
  try{const state=await(await fetch('/api/state',{cache:'no-store'})).json();if(state.habitat)window.BrainControls.update(state.habitat,state.status==='running',state.metadata,state.latest);}catch{}
}
