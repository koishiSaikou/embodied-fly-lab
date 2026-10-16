// Shared map coordinates and scene-editor UI. No browser-side physics.
export function toMap(x,y,half=25){return [35+(x+half)/(2*half)*530,35+(half-y)/(2*half)*530];}
export function fromMap(clientX,clientY,rect,half=25){
  const u=(clientX-rect.left)/rect.width*600,v=(clientY-rect.top)/rect.height*600;
  return {x:((u-35)/530-.5)*2*half,y:(.5-(v-35)/530)*2*half};
}
let kinds={};
// Distinct silhouettes remain legible without relying on category colors.
function drawPropIcon(c,kind){
  const ellipse=(x,y,a,b,color,angle=0)=>{c.fillStyle=color;c.beginPath();c.ellipse(x,y,a,b,angle,0,Math.PI*2);c.fill();};
  const line=(points,color,width=.10)=>{c.strokeStyle=color;c.lineWidth=width;c.beginPath();points.forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y));c.stroke();};
  const box=(x,y,w,h,color)=>{c.fillStyle=color;c.fillRect(x,y,w,h);};
  c.lineJoin='round';c.lineCap='round';
  switch(kind){
    case 'sugar':
      box(-.8,-.53,1.6,1.1,'#9b652d');
      for(const [x,y]of [[-.64,-.4],[.05,-.34],[-.25,.10]]){
        box(x,y,.55,.52,'#fff1ca');line([[x,y],[x+.55,y],[x+.55,y+.52]],'#d0ae72',.055);
      }break;
    case 'fruit':
      ellipse(-.23,.13,.46,.59,'#f15b36',-.2);ellipse(.23,.13,.46,.59,'#ff883b',.2);
      line([[0,-.36],[.08,-.78]],'#865a32',.12);ellipse(.30,-.62,.30,.13,'#7fbd55',-.35);
      ellipse(-.36,-.03,.08,.19,'#ffd49b',.2);break;
    case 'cool':
      for(let i=0;i<6;i++){const a=i*Math.PI/3;line([[0,0],[.75*Math.cos(a),.75*Math.sin(a)]],'#b8e9ff',.13);}
      break;
    case 'water':
      // A pointed droplet, instead of another round surface patch.
      c.fillStyle='#58b9ed';c.beginPath();c.moveTo(0,-.90);c.bezierCurveTo(-.28,-.40,-.75,.12,-.58,.52);
      c.bezierCurveTo(-.40,.98,.40,.98,.58,.52);c.bezierCurveTo(.75,.12,.28,-.40,0,-.90);c.fill();
      line([[-.37,.18],[-.39,.36],[-.28,.51]],'#d5f4ff',.09);break;
    case 'danger':
      c.fillStyle='#f5cc4e';c.strokeStyle='#332c26';c.lineWidth=.10;c.beginPath();c.moveTo(0,-.88);c.lineTo(.85,.69);c.lineTo(-.85,.69);c.closePath();c.fill();c.stroke();
      box(-.075,-.30,.15,.48,'#332c26');ellipse(0,.42,.085,.085,'#332c26');break;
    case 'light':
      for(let i=0;i<8;i++){const a=i*Math.PI/4;line([[.65*Math.cos(a),-.18+.65*Math.sin(a)],[.87*Math.cos(a),-.18+.87*Math.sin(a)]],'#ffe49a',.07);}
      ellipse(0,-.21,.43,.47,'#ffe49a');box(-.21,.18,.42,.31,'#ffe49a');box(-.21,.50,.42,.13,'#97a5aa');box(-.13,.67,.26,.09,'#97a5aa');break;
    case 'shade':
      line([[0,-.15],[0,.76],[.24,.85],[.37,.70]],'#e5d2ac',.09);
      c.fillStyle='#54a9ac';c.beginPath();c.moveTo(-.88,-.05);c.bezierCurveTo(-.66,-1.05,.66,-1.05,.88,-.05);
      c.quadraticCurveTo(.59,-.32,.30,-.05);c.quadraticCurveTo(0,-.32,-.30,-.05);c.quadraticCurveTo(-.59,-.32,-.88,-.05);c.fill();
      line([[0,-.73],[0,-.13]],'#b7e0d6',.055);break;
    default:
      box(-.65,-.65,1.3,1.3,'#dce3d5');box(-.59,-.59,.59,.59,'#38464c');box(0,0,.59,.59,'#38464c');break;
  }
}
export function drawElements(c,elements,half=25){
  const ordered=[...elements].sort((a,b)=>Number(!['light','shade'].includes(a.kind))-Number(!['light','shade'].includes(b.kind)));
  for(const element of ordered){
    const [x,y]=toMap(element.x,element.y,half),r=element.radius/(2*half)*530;
    const selected=globalThis.ArenaUI?.selected()===element.id;
    c.save();c.strokeStyle=selected?'#ffffff':'#7c939e';c.lineWidth=selected?2:1;
    c.globalAlpha=selected?.95:.55;c.setLineDash([3,4]);c.beginPath();c.arc(x,y,r,0,2*Math.PI);c.stroke();c.setLineDash([]);
    c.globalAlpha=element.strength===0?.40:1;c.translate(x,y);c.scale(r*.83,r*.83);drawPropIcon(c,element.kind);c.restore();
    c.save();c.font='11px "Microsoft YaHei",sans-serif';c.textAlign='center';c.fillStyle='#e4edf2';c.fillText(element.label,x,y-r-5);c.restore();
  }
}

const root=globalThis.document?.getElementById('arenaTools');
if(root)start().catch(error=>{root.textContent='场景编辑暂不可用：'+error.message;});
async function start(){
  const response=await fetch('/api/arena',{cache:'no-store'});
  if(!response.ok)throw Error('请先保存实验，退出旧服务并重新启动 Start-Habitat.cmd');
  const schema=await response.json();kinds=schema.kinds;
  root.innerHTML=`<div class="ae-heading"><strong>场景编辑</strong><span id="ae-count">—</span></div>
    <form id="ae-form"><fieldset id="ae-fields" disabled class="ae-fields">
    <label>选中元素<select id="ae-selected"></select></label><label>添加 / 修改类型<select id="ae-kind"></select></label>
    <label>名称<input id="ae-label" maxlength="24" required></label><label>半径 / mm<input id="ae-radius" type="number" min="0.8" max="4" step="0.1" value="2.5" required></label>
    <label>强度 / 0–1<input id="ae-strength" type="number" min="0" max="1" step="0.1" value="1" required></label>
    <div class="ae-buttons"><button type="button" id="ae-add">点击地图新增</button><button type="button" id="ae-move">点击地图移动</button><button type="submit">应用属性</button><button type="button" id="ae-delete">删除选中</button><button type="button" id="ae-cancel">取消放置</button></div>
    </fieldset></form><p class="caption ae-status" id="ae-status" role="status">选择类型后点击“新增”，再在地图空地放置；普通点击可选中区域。</p>
    <p class="caption">糖块、水果、水洼、警示牌、灯、遮阳棚、冰块与棋盘格标识元素；虚线圈表示作用范围。亮区/阴影可叠加。主要食物不能删除，强度 0 可关闭其输入。</p>`;
  const $=id=>document.getElementById('ae-'+id);
  for(const [key,profile]of Object.entries(kinds))$('kind').add(new Option(profile.label,key));
  let latest=null,running=false,selection='food',picking=null,dirty=false,pending=null,lastLayout='',directory=null;
  function selected(){return latest?.config.elements.find(e=>e.id===selection);}
  function fill(){const element=selected();if(!element)return;$('kind').value=element.kind;$('label').value=element.label;$('radius').value=element.radius;$('strength').value=element.strength;dirty=false;}
  function values(){return {kind:$('kind').value,label:$('label').value.trim(),radius:$('radius').valueAsNumber,strength:$('strength').valueAsNumber};}
  function say(text){$('status').textContent=text;}
  function cancel(){picking=null;$('cancel').disabled=true;}
  async function send(payload){
    if(!running||pending)return false;
    pending={revision:latest.arena_revision||0,ids:new Set(latest.config.elements.map(e=>e.id)),action:payload.action};$('fields').disabled=true;
    try{const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const result=await response.json();if(!response.ok)throw Error(result.error||response.status);say('已提交，等待场景块确认。');return true;
    }catch(error){pending=null;$('fields').disabled=!running;say('操作失败：'+error.message);return false;}
  }
  for(const id of ['label','radius','strength'])$(id).oninput=()=>{dirty=true;};
  $('kind').onchange=()=>{$('label').value=kinds[$('kind').value].label;dirty=true;};
  $('selected').onchange=()=>{selection=$('selected').value;cancel();fill();window.dispatchEvent(new Event('arena-selection-change'));};
  $('add').onclick=()=>{if(!$('form').reportValidity())return;window.Workbench?.show('map');picking={action:'add',values:values()};$('cancel').disabled=false;say('在地图空地点击放置 '+picking.values.label+'。');};
  $('move').onclick=()=>{if(!$('form').reportValidity())return;window.Workbench?.show('map');picking={action:'update',id:selection,values:values()};$('cancel').disabled=false;say('在地图点击 '+selected().label+' 的新位置。');};
  $('cancel').onclick=()=>{cancel();say('已取消放置。');};
  $('form').onsubmit=event=>{event.preventDefault();if($('form').reportValidity()){cancel();send({type:'arena_edit',action:'update',id:selection,values:values()});}};
  $('delete').onclick=()=>{cancel();send({type:'arena_edit',action:'delete',id:selection});};
  const sensory=document.getElementById('multisensoryPanel');
  sensory.innerHTML=`<h3>多感官输入 · 简化环境场</h3><div id="ae-switches" class="ae-switches"></div><dl class="readings ae-readings">
    <dt>糖 / 果味气味 左 · 右</dt><dd id="ae-odor">—</dd><dt>排斥气味 左 · 右</dt><dd id="ae-repel">—</dd><dt>亮度代理 左 · 右</dt><dd id="ae-light">—</dd><dt>湿度代理 左 · 右</dt><dd id="ae-humidity">—</dd><dt>温度代理 左 · 右</dt><dd id="ae-temp">—</dd><dt>甜味 / 苦味接触</dt><dd id="ae-taste">—</dd><dt>足部水源接触</dt><dd id="ae-water">—</dd><dt>当前区域</dt><dd id="ae-zones">—</dd><dt>多感官转向贡献</dt><dd id="ae-turn">—</dd></dl><p class="caption">字段在实际触角/足部位置采样，反馈经工程策略进入 P9；光照、湿度和温度为代理场，水源没有流体模型。视觉仍来自实际复眼画面。</p>`;
  const switches={light_enabled:'亮度引导',humidity_enabled:'湿度 / 水源反馈',thermal_enabled:'温度回避',bitter_enabled:'苦味回避'};
  for(const [key,label]of Object.entries(switches)){
    const row=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.id='ae-'+key;
    row.append(input,document.createTextNode(label));$('switches').appendChild(row);
    input.onchange=async()=>{try{const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'sensory_settings',values:{[key]:input.checked}})});const result=await response.json();if(!response.ok)throw Error(result.error||response.status);}catch(error){say('感觉开关失败：'+error.message);}};
  }
  const pair=(value,digits=3)=>Array.isArray(value)?value.map(x=>Number(x).toFixed(digits)).join(' / '):'—';
  window.ArenaUI={kinds,selected:()=>selection,movePrimary(){selection='food';$('selected').value='food';fill();$('move').click();},
    handleClick(point){
      if(!running||!latest)return false;
      if(picking){const action=picking;send({type:'arena_edit',...action,values:{...action.values,x:point.x,y:point.y}}).then(ok=>{if(ok)cancel();});return true;}
      const hit=latest.config.elements.filter(e=>Math.hypot(point.x-e.x,point.y-e.y)<=e.radius).sort((a,b)=>Number(['light','shade'].includes(a.kind))-Number(['light','shade'].includes(b.kind)))[0];
      if(hit){window.Workbench?.inspector('arena');selection=hit.id;$('selected').value=selection;fill();window.dispatchEvent(new Event('arena-selection-change'));return true;}return false;
    },update(h,isRunning){
      latest=h;running=isRunning;
      if(!Array.isArray(h.config.elements)){say('当前进程仍是旧版，请重启 Start-Habitat.cmd。');return;}
      if(directory!==h.directory){directory=h.directory;selection='food';dirty=false;pending=null;lastLayout='';cancel();}
      if(pending&&(h.arena_revision||0)>pending.revision){
        if(pending.action==='add')selection=h.config.elements.find(e=>!pending.ids.has(e.id))?.id||selection;
        pending=null;dirty=false;say('场景修改已应用，并写入本轮记录。');
      }
      const layout=JSON.stringify(h.config.elements);
      if(layout!==lastLayout){lastLayout=layout;if(!h.config.elements.some(e=>e.id===selection))selection='food';$('selected').replaceChildren();for(const e of h.config.elements)$('selected').add(new Option(e.label+' · '+kinds[e.kind].label,e.id));$('selected').value=selection;if(!dirty)fill();}
      $('fields').disabled=!running||!!pending;$('delete').disabled=selection==='food';$('cancel').disabled=!picking;
      $('count').textContent=h.config.elements.length+' / '+schema.max_elements;
      for(const key of Object.keys(switches)){const input=$(key);input.checked=!!h.config[key];input.disabled=!running||!h.config.multisensory_enabled;}
      const c=h.sense.channels||{};
      $('odor').textContent=pair(c.sugar_odor)+' · '+pair(c.fruit_odor);$('repel').textContent=pair(c.repellent_odor);
      $('light').textContent=pair(c.light);$('humidity').textContent=pair(c.humidity);$('temp').textContent=pair(c.temperature_c,1)+' °C（代理）';
      $('taste').textContent=Number(h.sense.taste?c.sweetness||0:0).toFixed(2)+' / '+Number(c.bitter||0).toFixed(2);
      $('water').textContent=h.sense.channels_raw?.water_contact?'有接触（原始采样）':'无接触';$('zones').textContent=(c.contacts||[]).map(id=>h.config.elements.find(e=>e.id===id)?.label||id).join('、')||'无';
      $('turn').textContent=Number(h.command.sensory_turn||0).toFixed(3);
    }};
  // Initial telemetry can fail while the schema and form are already ready.
  // Keep their nodes alive so subsequent websocket snapshots can recover.
  try{const state=await(await fetch('/api/state',{cache:'no-store'})).json();if(state.habitat)window.ArenaUI.update(state.habitat,state.status==='running');}catch{}
}
