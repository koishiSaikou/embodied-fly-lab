import {makeFilter,aggregateWindow,typeStats,curveValues,typeColor} from './neural_activity_core.js';

const root=document.getElementById('neuralActivityPanel');
if(root)start().catch(error=>{root.textContent='分类活动暂不可用：'+error.message;console.error(error);});

async function start(){
  root.classList.add('neural-activity');
  root.innerHTML=`<div class="na-heading"><div><h2>全脑分类活动</h2><p class="na-text">按真实放电事件统计最近最多 1 秒模型时间 · 显示设置不改变脑仿真</p></div><button id="na-pause">暂停图表</button></div>
    <form id="na-filter" class="na-filters">
      <label>神经元大类<select id="na-class"><option value="">全部大类</option></select></label>
      <label>具体类型<input id="na-type" list="na-types" maxlength="100" placeholder="全部类型，或输入 DNp09、LB4b"><datalist id="na-types"></datalist></label>
      <label>曲线单位<select id="na-unit"><option value="mean">平均每神经元 Hz</option><option value="sum">群体总放电次数 / 秒</option></select></label>
      <label>模型时间窗<select id="na-window"><option value="100">最近 0.1 秒</option><option value="200">最近 0.2 秒</option><option value="500">最近 0.5 秒</option><option value="1000" selected>最近 1 秒</option></select></label>
      <label>栅格最多行数<select id="na-rows"><option value="50">50 行</option><option value="100">100 行</option><option value="300" selected>300 行</option></select></label>
      <label>图表刷新间隔<select id="na-refresh"><option value="250">0.25 秒</option><option value="500" selected>0.5 秒</option><option value="1000">1 秒</option><option value="2000">2 秒</option></select></label>
      <button type="submit">应用筛选</button><button type="button" id="na-reset">显示全部</button><button type="button" id="na-unknown">类型未知</button>
    </form>
    <p class="na-scope na-text" id="na-scope" role="status">加载完整分类索引…</p><p class="na-warning" id="na-warning" role="status"></p>
    <div class="na-metrics"><div><span>选中范围内神经元</span><strong id="na-population">—</strong></div><div><span>窗口内参与放电</span><strong id="na-active">—</strong></div><div><span>窗口内放电次数</span><strong id="na-spikes">—</strong></div><div><span>窗口内活跃类型</span><strong id="na-active-types">—</strong></div></div>
    <div class="na-plots"><section class="na-box"><h3>按大类着色的放电栅格</h3><p class="na-text" id="na-row-limit">每个点是一次放电，行按模型编号排列；最多显示 300 个活动行</p><canvas id="na-raster" aria-label="按神经元大类着色的真实放电栅格"></canvas><div id="na-raster-info" class="na-text na-raster-info"></div></section>
      <section class="na-box"><h3>各大类放电曲线</h3><p class="na-text">平均模式包含选中群体内未放电的神经元，适合比较大小不同的群体</p><canvas id="na-class-curve" aria-label="各神经元大类的放电曲线"></canvas><div id="na-class-legend" class="na-legend"></div></section></div>
    <div class="na-tables"><section class="na-box"><h3>各大类统计</h3><p class="na-text">点击一行筛选该类；零放电也保留显示</p><div class="na-scroll"><table class="na-table"><thead><tr><th>大类</th><th>神经元数</th><th>活跃数</th><th>放电次数</th><th>平均 Hz</th></tr></thead><tbody id="na-class-rows"></tbody></table></div></section>
      <section class="na-box"><h3>具体类型曲线</h3><p class="na-text">显示选中范围内本窗放电最多的三个类型；可输入类型或点击下方类型行聚焦</p><canvas id="na-type-curve" aria-label="具体神经元类型的放电曲线"></canvas><div id="na-type-legend" class="na-type-legend"></div></section></div>
    <div class="na-tables"><section class="na-box"><h3>具体类型排行</h3><p class="na-text" id="na-type-info">最多显示 20 类，未知类型单列</p><div class="na-scroll"><table class="na-table"><thead><tr><th>具体类型</th><th>神经元数</th><th>活跃数</th><th>放电次数</th><th>平均 Hz</th></tr></thead><tbody id="na-type-rows"></tbody></table></div></section>
      <section class="na-box"><h3>选中范围内的活跃神经元</h3><p class="na-text">按本窗放电次数排序，最多显示 12 个；点击查看身份、定位已下载骨架</p><div class="na-scroll"><table class="na-table na-neurons"><thead><tr><th>FlyWire ID</th><th>类型</th><th>大类</th><th>次数</th><th>Hz</th></tr></thead><tbody id="na-neuron-rows"></tbody></table></div><p id="na-neuron-detail" class="na-text na-neuron-detail">请选择一个神经元。没有下载骨架的神经元仍参与分类统计。</p></section></div>
    <details class="na-p9"><summary>P9 运动接口 · 单独查看通道 A / B</summary><p class="na-text">保留原有刺激、放电和膜电位指标。通道 A/B 不等同于注释的解剖左右；步态仍通过工程映射驱动。</p><dl id="na-p9-values"></dl><canvas id="na-p9-curve" aria-label="P9 两个控制通道的实际放电率"></canvas></details>
    <p class="na-text na-foot">分类覆盖全部 138,639 个模型节点。大类未知与具体类型未知分别统计；三维骨架仍限于已下载的 16 个。筛选只影响显示。</p>`;
  window.Workbench?.mountActivity(root);
  const $=id=>document.getElementById('na-'+id),fmt=n=>Number(n).toLocaleString('zh-CN');
  const number=n=>!n?'0':Math.abs(n)<.001?n.toExponential(1):Math.abs(n)<1?n.toFixed(3):n.toFixed(1);
  const response=await fetch('/assets/activity_catalog.json');
  if(!response.ok)throw Error('分类索引未准备好，请运行 prepare_activity_catalog.py');
  const catalog=await response.json();
  if(catalog.class_of.length!==catalog.model_nodes||catalog.type_of.length!==catalog.model_nodes||catalog.ids.length!==catalog.model_nodes)throw Error('分类索引长度错误');
  for(const c of catalog.classes)$('class').add(new Option(`${c.label} (${fmt(c.population)})`,c.key));
  let filter=makeFilter(catalog),state=null,displayed=null,stats=null,paused=false,revision=-1;
  let typeRows=[],rasterRows=[],rasterBounds=null;
  try{const saved=JSON.parse(localStorage.getItem('flywire-activity-display')||'{}');for(const key of ['unit','window','rows','refresh'])if([...$(key).options].some(o=>o.value===saved[key]))$(key).value=saved[key];}catch{}
  for(const key of ['unit','window','rows','refresh'])$(key).onchange=()=>{
    try{localStorage.setItem('flywire-activity-display',JSON.stringify(Object.fromEntries(['unit','window','rows','refresh'].map(id=>[id,$(id).value]))));}catch{}
    if(displayed)render(displayed);
  };
  const names=catalog.types.map(t=>t.label.toLocaleLowerCase());
  const classLabel=c=>`${c.label} · ${c.key.startsWith('__')?'无注释':c.key}`;
  function choose(classKey,query){$('class').value=classKey;$('type').value=query==='__unknown_type__'?'类型未知':query;apply();}
  function apply(){
    filter=makeFilter(catalog,$('class').value,$('type').value);
    window.dispatchEvent(new CustomEvent('neural-filter-change',{detail:{matches:index=>!!filter.mask[index],population:filter.population}}));
    if(displayed)render(displayed);
  }
  $('filter').onsubmit=e=>{e.preventDefault();apply();};$('class').onchange=apply;
  $('reset').onclick=()=>choose('','');$('unknown').onclick=()=>choose('','__unknown_type__');
  $('type').oninput=()=>{
    const query=$('type').value.trim().toLocaleLowerCase();$('types').replaceChildren();
    let shown=0;for(let i=0;i<names.length&&shown<30;i++)if(!query||names[i].includes(query)){
      const option=document.createElement('option');option.value=catalog.types[i].key;option.label=catalog.types[i].label;$('types').appendChild(option);shown++;
    }
  };
  $('pause').onclick=()=>{paused=!paused;$('pause').textContent=paused?'继续图表':'暂停图表';if(!paused&&state){displayed=state;render(state);}};
  function canvas(id){
    const el=$(id),w=el.clientWidth,h=el.clientHeight,d=Math.min(devicePixelRatio,2);el.width=Math.round(w*d);el.height=Math.round(h*d);
    const c=el.getContext('2d');c.scale(d,d);c.font='10px "Segoe UI","Microsoft YaHei",sans-serif';return{c,w,h};
  }
  function axes(c,w,h,start,end,max,label){
    const p={left:65,right:w-14,top:23,bottom:h-34};c.strokeStyle='#30424f';c.fillStyle='#a8bac6';
    c.fillText(label,4,12);for(let i=0;i<=4;i++){const y=p.bottom-(p.bottom-p.top)*i/4;c.beginPath();c.moveTo(p.left,y);c.lineTo(p.right,y);c.stroke();c.fillText(number(max*i/4),3,y+3);}
    for(let i=0;i<=4;i++){const x=p.left+(p.right-p.left)*i/4;c.textAlign='center';c.fillText(((start+(end-start)*i/4)/1000).toFixed(2),x,h-15);}c.textAlign='right';c.fillText('模型秒',p.right,h-1);c.textAlign='left';return p;
  }
  function curves(id,series,start,end){
    const {c,w,h}=canvas(id),max=Math.max(0,...series.flatMap(s=>s.values))||1;
    const p=axes(c,w,h,start,end,max,$('unit').value==='mean'?'Hz / 神经元':'次 / 秒');
    for(const s of series){c.strokeStyle=s.color;c.lineWidth=1.8;c.beginPath();s.values.forEach((value,i)=>{
      const x=p.left+(i+.5)/s.values.length*(p.right-p.left),y=p.bottom-value/max*(p.bottom-p.top);i?c.lineTo(x,y):c.moveTo(x,y);
    });c.stroke();}
    if(!series.length){c.fillStyle='#a8bac6';c.fillText('当前范围没有放电事件',p.left+12,p.top+28);}
  }
  function legend(id,series){
    $(id).replaceChildren();for(const s of series){const span=document.createElement('span'),dot=document.createElement('i');dot.style.background=s.color;span.append(dot,document.createTextNode(s.label));span.title=s.label;$(id).appendChild(span);}
  }
  function raster(){
    const limit=Number($('rows').value),{c,w,h}=canvas('raster');rasterRows=[...stats.neuronSpikes.keys()].sort((a,b)=>a-b).slice(0,limit);
    $('row-limit').textContent=`每个点是一次放电，行按模型编号排列；最多显示 ${limit} 个活动行`;
    const rows=new Map(rasterRows.map((n,i)=>[n,i])),p=axes(c,w,h,stats.start,stats.end,Math.max(1,rasterRows.length),'活动行');rasterBounds=p;
    for(const [time,index]of stats.events){if(!rows.has(index))continue;
      const x=p.left+(time-stats.start)/stats.durationMs*(p.right-p.left),y=p.bottom-(rows.get(index)+.5)/Math.max(1,rasterRows.length)*(p.bottom-p.top);
      c.fillStyle=catalog.classes[catalog.class_of[index]].color;c.fillRect(x,y-1,2,2);
    }
    $('raster-info').textContent=`${fmt(stats.totalSpikes)} 个选中事件 · ${fmt(stats.active)} 个活跃神经元`+(stats.active>limit?` · 栅格仅显示编号最前的 ${limit} 行，统计仍包括所有采集到的事件`:'');
  }
  function table(id,rows,select,count){
    $(id).replaceChildren();for(let i=0;i<count;i++){
      const tr=document.createElement('tr'),row=rows[i];
      if(row){tr.tabIndex=0;const cells=[row.label,fmt(row.population),fmt(row.active),fmt(row.spikes),number(row.meanHz)];
        for(const text of cells){const td=tr.insertCell();td.textContent=text;td.title=text;}
        tr.onclick=()=>select(row);tr.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();select(row);}};
      }else {const td=tr.insertCell();td.colSpan=5;td.textContent=i===0?'当前范围无匹配类型':'\u00a0';}
      $(id).appendChild(tr);
    }
  }
  function render(s){
    if(!s.latest){$('scope').textContent=s.status==='error'?'实验异常停止，尚无成功脑活动数据':s.status==='saving'?'正在收尾保存，尚无成功脑活动数据':'等待第一块脑活动数据…';return;}
    if(s.metadata.neurons!==catalog.model_nodes)throw Error('当前模型节点数量与分类索引不匹配');
    if(s.metadata.annotations?.source_sha256&&s.metadata.annotations.source_sha256!==catalog.source_sha256)throw Error('注释来源与分类索引不匹配，请重建分类索引');
    stats=aggregateWindow(s,catalog,filter,Number($('window').value));if(!stats)return;
    const scope=[$('class').selectedOptions[0].textContent,filter.query?`类型 ${filter.query}`:'全部类型'].join(' / ');
    const phase={finished:'实验已结束，显示保存的最后时间窗',error:'实验异常停止，显示最后成功时间窗',saving:'正在收尾保存，显示最后成功时间窗',interrupted:'用户已停止，显示最后成功时间窗'};
    $('scope').textContent=`${scope} · 模型 ${(stats.start/1000).toFixed(2)}–${(stats.end/1000).toFixed(2)} s · ${phase[s.status]||(paused?'图表已暂停':'随模型更新')}`;
    $('warning').textContent=stats.complete?'':`事件缓冲不完整：全模型本窗采集到 ${fmt(stats.observedSpikes)} / 应有 ${fmt(stats.expectedSpikes)} 次放电。以下分组计数、活跃数与曲线仅为已采集事件的下限。`;
    for(const [id,value]of Object.entries({population:stats.population,active:stats.active,spikes:stats.totalSpikes,'active-types':stats.activeTypes}))$(id).textContent=fmt(value);
    const mean=$('unit').value==='mean';
    const classSeries=stats.classes.filter(g=>g.population>0).map(g=>({label:classLabel(g),color:g.color,values:curveValues(stats.classBins[g.index],stats.durationMs,g.population,mean)}));
    raster();curves('class-curve',classSeries,stats.start,stats.end);legend('class-legend',classSeries);
    table('class-rows',stats.classes,g=>choose(g.key,$('type').value),catalog.classes.length);
    const ids=catalog.types.map((_,i)=>i).filter(i=>filter.byType[i]>0).sort((a,b)=>stats.typeSpikes[b]-stats.typeSpikes[a]||filter.byType[b]-filter.byType[a]||a-b);
    const shown=ids.slice(0,20),unknown=catalog.types.length-1;
    if(filter.byType[unknown]>0&&!shown.includes(unknown)){if(shown.length===20)shown[19]=unknown;else shown.push(unknown);}
    typeRows=shown.map(i=>typeStats(catalog,filter,stats,i));table('type-rows',typeRows,g=>choose($('class').value,g.key),20);
    $('type-info').textContent=`选中范围共 ${fmt(ids.length)} 个类型组 · 活跃 ${fmt(stats.activeTypes)} 组 · 最多显示 20 组，未知类型保留单行`;
    const selected=ids.filter(i=>stats.typeSpikes[i]>0).slice(0,3);
    if(!selected.length&&filter.query)selected.push(...ids.slice(0,3));
    const typeSeries=selected.map(i=>({label:catalog.types[i].label,color:typeColor(i),values:curveValues(stats.typeBins.get(i)||new Uint32Array(10),stats.durationMs,filter.byType[i],mean)}));
    curves('type-curve',typeSeries,stats.start,stats.end);legend('type-legend',typeSeries);
    const ranked=[...stats.neuronSpikes].sort((a,b)=>b[1]-a[1]||a[0]-b[0]).slice(0,12);
    $('neuron-rows').replaceChildren();
    for(let i=0;i<12;i++){
      const tr=$('neuron-rows').insertRow(),item=ranked[i];if(!item){const td=tr.insertCell();td.colSpan=5;td.textContent=i?'\u00a0':'本窗无匹配放电';continue;}
      const [index,count]=item;for(const text of [catalog.ids[index],catalog.types[catalog.type_of[index]].label,catalog.classes[catalog.class_of[index]].label,fmt(count),number(count*1000/stats.durationMs)]){const td=tr.insertCell();td.textContent=text;td.title=text;}
      tr.tabIndex=0;tr.onclick=()=>inspect(index);tr.onkeydown=e=>{if(e.key==='Enter')inspect(index);};
    }
    p9(s);
    window.NeuralActivity.diagnostics={complete:stats.complete,observed:stats.observedSpikes,expected:stats.expectedSpikes,selectedSpikes:stats.totalSpikes,
      classSpikes:stats.classes.reduce((n,g)=>n+g.spikes,0),typeSpikes:stats.typeSpikes.reduce((a,b)=>a+b,0),population:stats.population,
      active:stats.active,activeTypes:stats.activeTypes,modelTime:stats.end,windowMs:stats.durationMs,rasterLimit:Number($('rows').value),refreshMs:Number($('refresh').value),classes:stats.classes.map(g=>({key:g.key,population:g.population,spikes:g.spikes,active:g.active})),typeRows:typeRows.map(g=>({key:g.key,population:g.population,spikes:g.spikes})),scope};
  }
  let inspectSerial=0;
  async function inspect(index){
    const serial=++inspectSerial,id=catalog.ids[index];
    try{const response=await fetch('/api/annotations/neuron/'+id);if(!response.ok)throw Error('身份查询失败');const row=await response.json();if(serial!==inspectSerial)return;
      const event=new CustomEvent('annotation-select',{detail:{row,note:''}});window.dispatchEvent(event);
      $('neuron-detail').textContent=`ID ${id} · ${row.cell_type||'类型未知'} · ${row.super_class||'大类未知'} · 预测递质 ${row.top_nt||'未知'}\n`+(event.detail.note||'可在身份查询中查看完整注释。');
    }catch(error){$('neuron-detail').textContent=error.message;}
  }
  $('raster').onclick=e=>{
    if(!rasterBounds||!rasterRows.length)return;const r=$('raster').getBoundingClientRect(),y=e.clientY-r.top,p=rasterBounds;
    if(y<p.top||y>p.bottom)return;const row=Math.floor((p.bottom-y)/(p.bottom-p.top)*rasterRows.length);
    if(rasterRows[row]!==undefined)inspect(rasterRows[row]);
  };
  function p9(s){
    const l=s.latest,a=l.activity||{},fields=[['刺激 A / B',(a.input_rate_hz||[]).map(number).join(' / ')+' Hz'],['实际放电 A / B',l.p9_hz.map(number).join(' / ')+' Hz'],['膜电位 A / B',(a.p9_voltage_mv||[]).map(number).join(' / ')+' mV']];
    $('p9-values').replaceChildren();for(const [label,value]of fields){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;$('p9-values').append(dt,dd);}
    if(!$('p9-curve').closest('details').open)return;
    const rows=s.history.filter(h=>h.brain_time_ms>=Math.max(0,l.brain_time_ms-2000));
    if(!rows.length)return;const {c,w,h}=canvas('p9-curve'),end=l.brain_time_ms,start=Math.max(0,end-2000),max=Math.max(100,...rows.flatMap(r=>r.p9_hz)),p=axes(c,w,h,start,end,max,'Hz');
    for(let side=0;side<2;side++){c.strokeStyle=['#efb76b','#86baff'][side];c.lineWidth=1.8;c.beginPath();rows.forEach((r,i)=>{const x=p.left+(r.brain_time_ms-start)/Math.max(1,end-start)*(p.right-p.left),y=p.bottom-r.p9_hz[side]/max*(p.bottom-p.top);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
  }
  $('p9-curve').closest('details').ontoggle=()=>{if(displayed)p9(displayed);};
  let resizeTimer;
  const observer=new ResizeObserver(()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(displayed)render(displayed);},50);});
  for(const id of ['raster','class-curve','type-curve'])observer.observe($(id));
  window.NeuralActivity={setFilter:choose,diagnostics:null};
  async function poll(){
    try{const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(10000)});if(!response.ok)throw Error('服务返回 '+response.status);const next=await response.json();state=next;
      if(!paused&&next.revision!==revision){revision=next.revision;displayed=next;render(next);}
    }catch(error){$('warning').textContent='无法更新脑活动：'+error.message;}
    setTimeout(poll,Number($('refresh').value));
  }
  poll();
}
