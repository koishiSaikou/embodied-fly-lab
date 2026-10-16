// Engineering behavior settings and external odor memory, never neural weights.
const root=document.getElementById('behaviorSettingsPanel');
const names={initializing:'等待输入',food_dwell:'食物接触停留',drink:'饮水停留',seek_water:'寻水',rest:'休息',groom:'右前足清洁',visual_startle:'视觉惊逃',
  seek_light:'趋光',avoid_bright:'趋暗 / 避强光',thermal_seek:'温度舒适趋向',upwind_track:'逆风追踪',odor_search:'局部气味搜索',learned_foraging:'偏好引导觅食',explore:'自由探索',
  hazard_escape:'危险后退',hazard_avoid:'危险回避',backup_reflex:'接触脱困',avoid_reflex:'接触转向',visual_avoid:'复眼避障',odor_explore:'气味觅食'};
if(root)start().catch(error=>{root.textContent='扩展行为暂不可用：'+error.message;});
async function start(){
  root.innerHTML=`<h2>行为与感官驱动</h2><p class="caption">需求与动作属于工程模型；气味学习使用外置记忆，不改写全脑连接。</p>
    <div class="bh-live"><strong id="bh-state">等待模型</strong><p id="bh-reason" class="caption"></p><div id="bh-meters"></div></div>
    <form id="bh-form" novalidate><fieldset id="bh-all" disabled><label class="bh-mode">当前模式<select id="bh-mode"></select></label>
      <details class="bc-group"><summary>综合探索与需要</summary><div id="bh-modes" class="bc-fields"></div></details>
      <details class="bc-group"><summary>环境反应与感官参数</summary><div id="bh-senses" class="bc-fields"></div></details>
      <details class="bc-group"><summary>觅食学习与记忆</summary><div id="bh-learning" class="bc-fields"></div><p class="caption">甜味接触提供奖励，苦味提供负反馈；最近气味痕迹参与更新。关闭学习会冻结并停用偏好。</p><dl class="readings"><dt>糖味价值 / 果味价值</dt><dd id="bh-values">—</dd><dt>学习更新 / 最近奖励</dt><dd id="bh-memory">—</dd></dl>
      <div class="bh-actions"><button type="button" data-bh-action="reward">奖励 +1</button><button type="button" data-bh-action="punish">惩罚 −1</button><button type="button" data-bh-action="clear">清空气味记忆</button></div><p class="caption">训练按钮作用于当前气味痕迹；没有痕迹时不更新。记忆保留在本轮记录，新一轮清空。</p></details>
      </fieldset><div class="bc-actions"><button id="bh-apply" type="submit" disabled>应用行为设置</button><button id="bh-defaults" type="button" disabled>恢复行为设置</button></div></form>
    <details class="bc-group"><summary>设置需要值 / 行为演示</summary><form id="bh-needs-form"><fieldset id="bh-needs" class="bc-fields" disabled></fieldset><button id="bh-needs-apply" type="submit" disabled>应用需要值</button></form><p class="caption">0–1 的工程状态。提高口渴可观察寻水；提高疲劳可观察休息；清洁先提供右前足抬头摆扫动作。</p></details>
    <p id="bh-status" role="status" class="bc-status caption">读取行为设置…</p><p id="bh-wind" class="caption"></p><p id="bh-groom" class="caption"></p>`;
  const $=id=>document.getElementById('bh-'+id),inputs=new Map(),needs=new Map();
  const response=await fetch('/api/behavior-controls',{cache:'no-store'});
  if(!response.ok)throw Error('当前服务未载入新行为，请结束保存后重启 Start-Habitat.cmd');
  const schema=await response.json();let running=false,dirty=false,needsDirty=false,pending=null,lastRevision=-1,directory=null,initialized=false;
  for(const field of schema.fields){
    if(field.key==='mode'){
      for(const [value,label]of Object.entries(field.choices))$('mode').add(new Option(label,value));inputs.set('mode',$('mode'));$('mode').onchange=()=>{dirty=true;};continue;
    }
    const label=document.createElement('label'),title=document.createElement('span'),input=document.createElement(field.kind==='choice'?'select':'input');title.textContent=field.label;input.id='bh-'+field.key;
    if(field.kind==='choice'){for(const [value,text]of Object.entries(field.choices))input.add(new Option(text,value));}
    else{input.type=field.kind==='boolean'?'checkbox':'number';if(input.type==='number'){input.min=field.minimum;input.max=field.maximum;input.step=field.step;input.required=true;}}
    input.oninput=()=>{dirty=true;$('status').textContent='有未应用的行为设置。';};label.append(title,input);$(field.group).append(label);inputs.set(field.key,input);
  }
  for(const [key,label]of Object.entries({hunger:'饥饿',thirst:'口渴',fatigue:'疲劳',dust:'清洁负荷'})){
    const meter=document.createElement('label');meter.innerHTML='<span></span><progress max="1"></progress><output></output>';meter.firstElementChild.textContent=label;meter.dataset.need=key;$('meters').append(meter);
    const row=document.createElement('label'),text=document.createElement('span'),input=document.createElement('input');text.textContent=label+' / 0–1';input.type='number';input.min=0;input.max=1;input.step=.01;input.required=true;input.oninput=()=>{needsDirty=true;};row.append(text,input);$('needs').append(row);needs.set(key,input);
  }
  function fill(values){for(const [key,input]of inputs)if(input.type==='checkbox')input.checked=values[key];else input.value=values[key];}
  function values(){return Object.fromEntries([...inputs].map(([key,input])=>[key,input.type==='checkbox'?input.checked:input.tagName==='SELECT'?input.value:input.valueAsNumber]));}
  async function send(payload){if(!running||pending)return;pending={revision:lastRevision,type:payload.type};$('all').disabled=true;
    $('status').textContent='正在提交，等待模型块确认…';
    try{const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(data.error||response.status);}
    catch(error){pending=null;$('all').disabled=!running;$('status').textContent='行为操作失败：'+error.message;}
  }
  $('form').onsubmit=event=>{event.preventDefault();const invalid=[...inputs.values()].find(input=>!input.checkValidity());if(invalid){const group=invalid.closest('details');if(group)group.open=true;invalid.reportValidity();return;}send({type:'behavior_settings',values:values()});};
  $('defaults').onclick=()=>{fill(schema.defaults);dirty=true;send({type:'behavior_settings',values:values()});};
  $('needs-form').onsubmit=event=>{event.preventDefault();send({type:'behavior_needs',values:Object.fromEntries([...needs].map(([key,input])=>[key,input.valueAsNumber]))});};
  for(const button of root.querySelectorAll('[data-bh-action]'))button.onclick=()=>send(button.dataset.bhAction==='clear'?{type:'behavior_memory_reset'}:{type:'behavior_reward',reward:button.dataset.bhAction==='reward'?1:-1});
  window.BehaviorUI={update(h,isRunning){
    running=isRunning&&h.config.multisensory_enabled;const settings=h.config.behavior_controls,b=h.behavior;if(!settings||!b){$('status').textContent='当前进程未载入新行为，请结束保存并重新启动。';return;}
    if(directory!==h.directory){directory=h.directory;dirty=needsDirty=false;pending=null;initialized=false;}
    lastRevision=h.behavior_revision||0;
    if(pending&&lastRevision>pending.revision){if(pending.type==='behavior_settings')dirty=false;if(pending.type==='behavior_needs')needsDirty=false;pending=null;$('status').textContent=h.paused?'已应用；继续后按新状态计算。':'行为操作已应用。';}
    if(!dirty&&!pending)fill(settings);
    if(!needsDirty&&!pending)for(const [key,input]of needs)input.value=Number(b.needs[key]).toFixed(2);
    if(!initialized){$('status').textContent='已同步。修改模式或开关后点击“应用行为设置”。';initialized=true;}
    $('all').disabled=$('needs').disabled=!running||!!pending;for(const button of [$('apply'),$('defaults'),$('needs-apply'),...root.querySelectorAll('[data-bh-action]')])button.disabled=!running||!!pending;
    $('state').textContent=b.enabled?(names[b.state]||b.state):'扩展行为已关闭';$('reason').textContent=b.enabled?b.reason:'使用原来的觅食与避障策略。';
    for(const meter of $('meters').children){const v=b.needs[meter.dataset.need];meter.querySelector('progress').value=v;meter.querySelector('output').textContent=Math.round(v*100)+'%';}
    $('values').textContent=b.odor_values.sugar.toFixed(3)+' / '+b.odor_values.fruit.toFixed(3);$('memory').textContent=b.learning_updates+' / '+b.last_reward.toFixed(3);
    const w=h.sense.channels?.wind;$('wind').textContent=w?`风向代理：吹向 ${w.direction_deg.toFixed(0)}°，强度 ${w.strength.toFixed(2)}；没有流体或风力物理。`:'';
    $('groom').textContent=h.sense.foreleg_head_distance_mm?'右前足距头部 '+h.sense.foreleg_head_distance_mm[1].toFixed(2)+' mm（实际物理位置）':'';
    if(!isRunning)$('status').textContent='本轮已保存；新一轮会重置需要值与气味记忆。';
    else if(!h.config.multisensory_enabled)$('status').textContent='当前为 classic 基线；请用默认综合觅食场启动后使用扩展行为。';
  }};
  try{const s=await(await fetch('/api/state',{cache:'no-store'})).json();if(s.habitat)window.BehaviorUI.update(s.habitat,s.status==='running');}catch{}
}
