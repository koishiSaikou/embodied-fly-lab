import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import {drawElements,fromMap} from './arena_tools.js?v=behavior-20261016';

const $ = id => document.getElementById(id);
const names = {drink:'饮水停留 · 工程状态',seek_water:'寻水 · 湿度代理',rest:'休息 · 工程状态',groom:'右前足清洁 · 动作程序',visual_startle:'视觉惊逃 · 复眼反射',seek_light:'趋光 · 明暗代理',avoid_bright:'趋暗 / 避强光',thermal_seek:'温度舒适趋向',upwind_track:'逆风追踪 · 风向代理',odor_search:'局部气味搜索',learned_foraging:'外置偏好引导觅食',explore:'自由探索',visual_avoid:'复眼视觉避障 · 工程反射',odor_explore:'气味引导探索 · 工程策略',explore_without_odor:'无气味探索 · 工程策略',
  hazard_escape:'苦味 / 高温后退 · 工程反射',hazard_avoid:'危险场回避 · 工程策略',water_contact:'水源接触减速 · 工程策略',multisensory_explore:'多感官引导 · 工程策略',
  avoid_reflex:'近距转向 · 工程反射',backup_reflex:'后退脱困 · 工程反射',food_dwell:'食物接触停留 · 显式规则'};
let latest = null, placing = false, following = true, worldItems = [], brainRegions = [], neurons = [];
let lastHistory=[];
let lastRetina = -1, lastRevision = -1, lastTarget = null, modelTime = 0, lastStatus='loading';
let worldView, brainView, brainCamera, brainControls, brainData, worldControls, camera;
const spikeTimes = new Map();
let selectedNeuronId=null;
let neuralFilter=index=>true,latestNeuralChunk=null;
window.addEventListener('neural-filter-change',event=>{
  neuralFilter=event.detail.matches;
  for(const n of neurons)n.line.visible=neuralFilter(n.index);
  updateSkeletonReadout();
});
window.addEventListener('annotation-select',event=>{
  const n=neurons.find(n=>n.id===event.detail.row.flywire_id);
  if(!n||!brainControls){event.detail.note='该神经元的身份可查询；尚未下载其三维骨架。';return;}
  if(!neuralFilter(n.index))window.NeuralActivity?.setFilter('',event.detail.row.cell_type||'__unknown_type__');
  selectedNeuronId=n.id;
  n.line.geometry.computeBoundingBox();
  const target=n.line.geometry.boundingBox.getCenter(new THREE.Vector3());
  brainCamera.position.add(target.clone().sub(brainControls.target));brainControls.target.copy(target);
  brainCamera.zoom=1.8;brainCamera.updateProjectionMatrix();
  event.detail.note='已定位三维骨架（橙色为选中项，青色仍表示实际放电）。';
  window.labDiagnostics.selectedNeuronId=n.id;
});
const scene = new THREE.Scene();
scene.background = new THREE.Color('#b9bcad');
scene.fog = new THREE.Fog('#b9bcad',65,145);

function view(container, scene, camera, options={}) {
  const renderer = new THREE.WebGLRenderer({antialias:true, ...options});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.75));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  container.appendChild(renderer.domElement);
  const resize = () => {
    const w=container.clientWidth,h=container.clientHeight;
    if(w<=0||h<=0)return;
    renderer.setSize(w,h,false);
    if(camera.isPerspectiveCamera) camera.aspect=w/h;
    else {const half=Math.max(220,440/(w/h));camera.left=-half*w/h;camera.right=half*w/h;camera.top=half;camera.bottom=-half;}
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(container);
  resize();
  return renderer;
}

const propGeometries=new Map();
function propGeometry(type){
  if(!propGeometries.has(type)){
    let geometry;
    if(type===4)geometry=new THREE.SphereGeometry(1,20,14);
    else if(type===5){geometry=new THREE.CylinderGeometry(1,1,2,32);geometry.rotateX(Math.PI/2);}
    else if(type===6)geometry=new THREE.BoxGeometry(2,2,2);
    else throw Error('Unsupported arena primitive: '+type);
    propGeometries.set(type,geometry);
  }
  return propGeometries.get(type);
}
function primitive(g) {
  if(g.dynamic)return propGeometry(g.type);
  const [a,b,c]=g.size;
  switch(g.type) {
    case 0: return new THREE.PlaneGeometry(80,80);
    case 2: return new THREE.SphereGeometry(a,18,12);
    case 3: {const x=new THREE.CapsuleGeometry(a,2*b,6,12);x.rotateX(Math.PI/2);return x;}
    case 4: {const x=new THREE.SphereGeometry(1,18,12);x.scale(a,b,c);return x;}
    case 5: {const x=new THREE.CylinderGeometry(a,a,2*b,48);x.rotateX(Math.PI/2);return x;}
    case 6: return new THREE.BoxGeometry(2*a,2*b,2*c);
    default:return null;
  }
}

async function loadWorld() {
  camera=new THREE.PerspectiveCamera(42,1,.015,250);
  camera.up.set(0,0,1);
  camera.position.set(-19,-16,4);
  worldView=view($('worldCanvas'),scene,camera);
  worldView.shadowMap.enabled=true;
  worldView.shadowMap.type=THREE.PCFSoftShadowMap;
  worldControls=new OrbitControls(camera,worldView.domElement);
  worldControls.enableDamping=true;worldControls.minDistance=2;worldControls.maxDistance=120;
  worldControls.maxPolarAngle=Math.PI*.49;
  scene.add(new THREE.HemisphereLight('#edf3ed','#847761',1.8));
  const light=new THREE.DirectionalLight('#fff1d3',2.2);
  light.position.set(-18,-12,40);light.castShadow=true;
  light.shadow.mapSize.set(2048,2048);
  Object.assign(light.shadow.camera,{left:-32,right:32,top:32,bottom:-32,near:.1,far:100});
  light.shadow.bias=-.00015;light.shadow.normalBias=.025;scene.add(light);
  let data;
  for(let attempt=0;attempt<60;attempt++) {
    data=await (await fetch('/api/scene')).json();
    if(data.geoms) break;
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  if(!data?.geoms) throw Error('身体网格尚未准备完成');
  const geometries=new Map();
  for(const [id,m] of Object.entries(data.meshes)) {
    const geometry=new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(m.vertices,3));
    geometry.setIndex(m.faces);geometry.computeVertexNormals();geometries.set(Number(id),geometry);
  }
  worldItems=data.geoms.map(g=>{
    const geometry=g.mesh>=0?geometries.get(g.mesh):primitive(g);
    if(!geometry)return null;
    const color=new THREE.Color().setRGB(...g.rgba.slice(0,3),THREE.SRGBColorSpace);
    const food=g.dynamic;
    const material=new THREE.MeshPhysicalMaterial({color,roughness:food?.22:.78,
      clearcoat:food?.6:0,metalness:0,side:THREE.DoubleSide,transparent:g.rgba[3]<1,
      opacity:g.rgba[3],depthWrite:g.rgba[3]>=1});
    const mesh=new THREE.Mesh(geometry,material);
    mesh.matrixAutoUpdate=false;mesh.castShadow=g.type!==0 && g.rgba[3]>.8;mesh.receiveShadow=true;
    mesh.name=g.name;mesh.userData.geomType=g.type;scene.add(mesh);return mesh;
  });
  $('sceneMessage').textContent='';
  if(latest)applyPose(latest.pose);
  window.labDiagnostics.scene={geoms:data.geoms.length,meshes:geometries.size,source:data.pose_source};
}

function applyPose(pose) {
  if(!pose || !worldItems.length)return;
  const appearances=new Map((pose.appearance||[]).map(a=>[a.index,a]));
  worldItems.forEach((mesh,i)=>{
    if(!mesh)return;
    const p=pose.position[i],r=pose.rotation[i];
    const appearance=appearances.get(i),type=appearance?.type??mesh.userData.geomType;
    const s=appearance?(type===5?[appearance.size[0],appearance.size[0],appearance.size[1]]:appearance.size):[1,1,1];
    if(appearance && mesh.userData.geomType!==type){mesh.geometry=propGeometry(type);mesh.userData.geomType=type;}
    mesh.matrix.set(r[0]*s[0],r[1]*s[1],r[2]*s[2],p[0],r[3]*s[0],r[4]*s[1],r[5]*s[2],p[1],r[6]*s[0],r[7]*s[1],r[8]*s[2],p[2],0,0,0,1);
    if(appearance){mesh.visible=appearance.rgba[3]>0;mesh.material.color.setRGB(...appearance.rgba.slice(0,3),THREE.SRGBColorSpace);
      mesh.material.opacity=appearance.rgba[3];const transparent=appearance.rgba[3]<1;
      if(mesh.material.transparent!==transparent){mesh.material.transparent=transparent;mesh.material.needsUpdate=true;}
      mesh.material.depthWrite=!transparent;mesh.castShadow=!transparent && appearance.size[2]>.1;}
    mesh.matrixWorldNeedsUpdate=true;
  });
}

const trailGeometry=new THREE.BufferGeometry();
const trailLine=new THREE.Line(trailGeometry,new THREE.LineBasicMaterial({color:'#577c69',transparent:true,opacity:.45}));
scene.add(trailLine);

async function loadBrain() {
  const response=await fetch('/assets/morphology.json');
  if(!response.ok)throw Error('解剖数据尚未准备完成');
  brainData=await response.json();
  const brainScene=new THREE.Scene();brainScene.background=new THREE.Color('#111b22');
  brainCamera=new THREE.OrthographicCamera(-320,320,320,-320,.1,5000);
  brainCamera.up.set(0,-1,0);brainCamera.position.set(0,0,-1100);
  brainView=view($('brainCanvas'),brainScene,brainCamera);
  brainControls=new OrbitControls(brainCamera,brainView.domElement);
  brainControls.enableDamping=true;brainControls.minZoom=.4;brainControls.maxZoom=5;
  brainScene.add(new THREE.AmbientLight('#c7e6f2',2));
  const light=new THREE.DirectionalLight('#c7e6f2',3);light.position.set(0,-300,-600);brainScene.add(light);
  for(const r of brainData.regions) {
    const geometry=new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(r.vertices,3));geometry.setIndex(r.faces);geometry.computeVertexNormals();
    const mesh=new THREE.Mesh(geometry,new THREE.MeshPhongMaterial({color:'#5e8095',transparent:true,opacity:.10,
      side:THREE.DoubleSide,depthWrite:false,shininess:12}));
    mesh.userData.name=r.name;brainScene.add(mesh);brainRegions.push(mesh);
    const wire=new THREE.LineSegments(new THREE.EdgesGeometry(geometry,28),new THREE.LineBasicMaterial({color:'#92b3c4',transparent:true,opacity:.11}));
    mesh.add(wire);
  }
  for(const n of brainData.neurons) {
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(n.segments,3));
    const line=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:'#b8c7ce',transparent:true,opacity:.62}));
    line.visible=neuralFilter(n.index);
    brainScene.add(line);neurons.push({...n,line});
  }
  $('morphCount').textContent=`${neurons.length} 个真实骨架`;
  $('brainInfo').textContent=`FAFB v783 骨架 · ${brainRegions.length} 个配准脑区。模板保持静态，骨架按实际放电显示并在100ms模型时间内衰减。`;
  const scale=document.createElement('div');scale.className='brain-scale';scale.textContent='100 μm';
  Object.assign(scale.style,{position:'absolute',right:'12px',bottom:'8px',fontSize:'9px',color:'#819caa',textAlign:'center',borderTop:'1px solid #819caa',paddingTop:'3px',pointerEvents:'none'});
  $('brainCanvas').appendChild(scale);
  brainView.scaleBar=scale;brainView.scene=brainScene;
  window.labDiagnostics.morphology={neurons:neurons.length,regions:brainRegions.length,ids:neurons.map(n=>n.id),version:brainData.dataset};
  updateSkeletonReadout();
}

function filterBrain() {
  const selected=$('region').value;
  brainRegions.forEach(mesh=>{
    const n=mesh.userData.name;
    const matches=selected==='all' || (selected==='MB'?/^(CA|PED|aL|bL|gL|a'L|b'L|MB)/i.test(n):
      selected==='CX'?/^(FB|EB|PB|NO)/.test(n):n.startsWith(selected));
    mesh.visible=!$('neuronsOnly').checked&&matches;
    mesh.material.opacity=selected==='all'?.10:.32;
  });
}

async function command(payload) {
  try {
    const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const result=await response.json();if(!response.ok)throw Error(result.error||response.status);
    $('message').textContent='操作已发送，等待下个模型时间块应用。';
  }catch(error){$('message').textContent='操作失败：'+error.message;}
}
$('pause').onclick=()=>latest&&command({type:'pause',enabled:!latest.paused});
$('stop').onclick=()=>command({type:['finished','error'].includes(lastStatus)?'restart':'stop'});
for(const type of ['vision','odor','contact','taste'])$(type).onchange=()=>command({type,enabled:$(type).checked});
$('follow').onclick=()=>{
  following=true;lastTarget=null;
  if(latest&&camera){const p=latest.sense.position_mm;camera.position.set(p[0]+.8,p[1]-6,p[2]+2.8);worldControls.target.set(...p);}
  $('follow').classList.add('active');$('overview').classList.remove('active');$('cameraLabel').textContent='跟随镜头';
};
$('overview').onclick=()=>{
  following=false;lastTarget=null;camera.position.set(30,-42,52);worldControls.target.set(0,0,0);
  $('follow').classList.remove('active');$('overview').classList.add('active');$('cameraLabel').textContent='场地全景';
};
$('fullscreen').onclick=()=>document.fullscreenElement?document.exitFullscreen():$('world').requestFullscreen();
$('region').onchange=filterBrain;$('neuronsOnly').onchange=filterBrain;
$('brainReset').onclick=()=>{selectedNeuronId=null;if(!brainCamera)return;brainCamera.position.set(0,0,-1100);brainCamera.up.set(0,-1,0);brainCamera.zoom=1;brainCamera.updateProjectionMatrix();brainControls.target.set(0,0,0);};
$('moveFood').onclick=()=>{window.Workbench?.show('map');if(window.ArenaUI){window.ArenaUI.movePrimary();return;}placing=!placing;$('moveFood').textContent=placing?'取消移动':'移动主要食物';$('message').textContent=placing?'点击下方地图的空白位置放置食物。':'';};
$('arena').onclick=event=>{
  if(!latest)return;
  const point=fromMap(event.clientX,event.clientY,$('arena').getBoundingClientRect(),latest.config.half_size);
  if(window.ArenaUI?.handleClick(point)){drawMap(latest);return;}
  if(!placing)return;
  const rect=$('arena').getBoundingClientRect(),u=(event.clientX-rect.left)/rect.width,v=(event.clientY-rect.top)/rect.height;
  command({type:'food',x:((u*600-35)/530-.5)*50,y:(.5-(v*600-35)/530)*50});
  placing=false;$('moveFood').textContent='移动食物';
};

function canvas2d(id,height) {
  const el=$(id),w=el.clientWidth,h=height||w,d=Math.min(devicePixelRatio,2);
  el.width=Math.round(w*d);el.height=Math.round(h*d);const context=el.getContext('2d');context.scale(d,d);
  return {context,w,h};
}

function drawMap(habitat) {
  const {context:c,w}=canvas2d('arena');c.scale(w/600,w/600);
  const map=(x,y)=>[35+(x+25)/50*530,35+(25-y)/50*530];
  c.fillStyle='#111c25';c.fillRect(0,0,600,600);c.fillStyle='#25323b';c.fillRect(35,35,530,530);
  c.strokeStyle='#354752';c.lineWidth=1;
  for(let v=-25;v<=25;v+=5){c.beginPath();c.moveTo(...map(v,-25));c.lineTo(...map(v,25));c.moveTo(...map(-25,v));c.lineTo(...map(25,v));c.stroke();}
  c.fillStyle='#69756d';
  for(const ob of habitat.config.obstacles){if(ob.type==='circle'){c.beginPath();c.arc(...map(ob.x,ob.y),ob.radius/50*530,0,Math.PI*2);c.fill();}
    else c.fillRect(...map(ob.x-ob.hx,ob.y+ob.hy),ob.hx*2/50*530,ob.hy*2/50*530);}
  if(habitat.config.elements)drawElements(c,habitat.config.elements,habitat.config.half_size);
  else for(const [xy,color] of [[[-13,14],'#889399'],[habitat.config.food,'#efb76b']]){c.fillStyle=color;c.beginPath();c.arc(...map(...xy),habitat.config.food_radius/50*530,0,Math.PI*2);c.fill();}
  const wind=habitat.sense.channels?.wind;
  if(wind?.strength>0){const a=wind.direction_deg*Math.PI/180,x=70,y=19,l=16+wind.strength*14;c.save();c.strokeStyle='#b8d9eb';c.fillStyle='#b8d9eb';c.lineWidth=1.5;c.beginPath();c.moveTo(x,y);c.lineTo(x+Math.cos(a)*l,y-Math.sin(a)*l);c.stroke();c.translate(x+Math.cos(a)*l,y-Math.sin(a)*l);c.rotate(-a);c.beginPath();c.moveTo(0,0);c.lineTo(-6,-3);c.lineTo(-6,3);c.fill();c.restore();c.font='10px sans-serif';c.fillStyle='#b8d9eb';c.fillText('风向代理',110,22);}
  c.strokeStyle='#83ddbe';c.lineWidth=2;c.beginPath();
  habitat.trail.forEach((r,i)=>{i?c.lineTo(...map(r[1],r[2])):c.moveTo(...map(r[1],r[2]));});c.stroke();
  c.save();c.translate(...map(...habitat.sense.position_mm));c.rotate(-habitat.sense.yaw_rad);c.fillStyle='#fff';c.beginPath();c.moveTo(11,0);c.lineTo(-8,-6);c.lineTo(-5,0);c.lineTo(-8,6);c.closePath();c.fill();c.restore();
  c.strokeStyle='#829aab';c.lineWidth=3;c.strokeRect(35,35,530,530);
}

// Redraw on layout changes even while the simulation is paused or finished.
function resizeMap(){if(latest)drawMap(latest);}
new ResizeObserver(resizeMap).observe($('arena'));
window.addEventListener('resize',resizeMap);
window.addEventListener('arena-selection-change',resizeMap);
window.addEventListener('workbench-layout',()=>{resizeMap();drawRate(lastHistory);});
new ResizeObserver(()=>drawRate(lastHistory)).observe($('rateChart'));

function drawRate(history) {
  lastHistory=history;
  const {context:c,w,h}=canvas2d('rateChart',$('rateChart').clientHeight||75);
  c.strokeStyle='#2d424e';for(let y=0;y<=h;y+=25){c.beginPath();c.moveTo(0,y);c.lineTo(w,y);c.stroke();}
  if(!history?.length)return;
  const rates=history.map(x=>x.chunk_spikes/(x.chunk_ms/1000));const max=Math.max(1,...rates);
  c.strokeStyle='#83ddbe';c.lineWidth=1.5;c.beginPath();
  rates.forEach((rate,i)=>{const x=i/Math.max(1,rates.length-1)*w,y=h-4-rate/max*(h-10);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();
  $('rate').textContent=Math.round(rates.at(-1)).toLocaleString()+' 次/模型秒';
}

let recoveryMessage=false;
function renderLifecycle(state) {
  lastStatus=state.status;
  window.ExperimentIO?.updateState(state);
  if(state.project){
    document.title=state.project.name+' · '+state.project.version;
    const version=document.getElementById('appVersion');
    if(version)version.textContent=`${state.project.name} ${state.project.version} · Demo · ${state.project.release_status==='demo-prerelease'?'公开测试版':state.project.release_status==='unpublished-demo'?'尚未公开发布':state.project.release_status}`;
  }
  const wait=state.brain_wait, running=state.status==='running', h=state.habitat;
  const labels={loading:'正在载入模型…',saving:'正在收尾保存…',finished:'实验已保存',error:'实验异常停止',interrupted:'用户已停止'};
  const pending=wait?`${wait.stage==='brain_startup'?'脑首次加载':'等待脑计算'} ${wait.elapsed_seconds.toFixed(1)} / ${wait.timeout_seconds} 秒`:null;
  $('status').textContent='● '+(pending|| (running?(h?.paused?'已暂停':'正在计算'):labels[state.status]||state.status));
  $('status').title=$('status').textContent;
  const restartable=state.recovery?.can_restart??state.status==='finished';
  $('pause').disabled=!running||!h;$('stop').disabled=!(running&&h)&&!restartable;
  $('stop').textContent=running?'结束并保存':'开始新一轮';
  $('stop').title=running?'结束本次实验并保存':'按初始配置开始新一轮，旧记录保留';
  for(const id of ['vision','odor','contact','taste','moveFood'])$(id).disabled=!running||!h;
  const error=state.failure?.message||state.cleanup_errors?.[0]?.message;
  if(error){
    $('message').textContent=`${error}。${state.status==='saving'?'正在尝试保存记录…':state.recovery?.can_restart?'可查看记录，或手动开始新一轮。':'请查看控制台及本轮日志。'}`;
    $('message').title=$('message').textContent;recoveryMessage=true;
  }else if(recoveryMessage){$('message').textContent='';$('message').title='';recoveryMessage=false;}
  if(!h&&state.directory){$('directory').textContent=state.directory;$('directory').title=state.directory;}
}

function render(state) {
  if(state.revision===lastRevision)return;lastRevision=state.revision;
  renderLifecycle(state);
  const h=state.habitat;if(!h)return;
  if(latest&&latest.directory!==h.directory){spikeTimes.clear();lastRetina=-1;lastTarget=null;}
  latest=h;modelTime=h.time_s;lastStatus=state.status;
  window.BrainControls?.update(h,state.status==='running',state.metadata,state.latest);
  window.NeuralIntervention?.update(h,state.status==='running');
  window.MBLearning?.update(h,state.status==='running');
  window.MBObserver?.update(h,state.status);
  window.ArenaUI?.update(h,state.status==='running');
  window.BehaviorUI?.update(h,state.status==='running');
  latestNeuralChunk=state.latest;
  window.NeuronAnnotations?.updateActivity(state.latest?.activity?.top_neurons);
  window.labDiagnostics.latest={time:h.time_s,poseTime:h.pose?.time_s,retinaTime:h.retina?.time_s,
    brainTime:state.latest?.brain_time_ms,paused:h.paused,status:state.status,food:h.config.food,drive:h.motor_drive};
  const running=state.status==='running';
  $('pause').textContent=h.paused?'继续':'暂停';
  $('time').textContent=h.time_s.toFixed(2);$('speed').textContent=(h.time_s/Math.max(.01,state.wall_seconds)).toFixed(2)+' × 实时';
  $('behavior').textContent=names[h.command.state]||h.command.state;
  const nearest='nearest_food_distance_mm' in h.sense?h.sense.nearest_food_distance_mm:h.sense.food_distance_mm;
  $('distance').textContent=nearest==null?'无有效食物':nearest.toFixed(1)+' mm';$('path').textContent=h.metrics.path_mm.toFixed(1)+' mm';
  $('firstFood').textContent=h.metrics.first_food_time_s==null?'尚未接触食物':'首次接触 '+h.metrics.first_food_time_s.toFixed(2)+' s';
  $('foodTime').textContent=h.metrics.food_zone_time_s.toFixed(2)+' s';$('contacts').textContent='碰撞片段 '+h.metrics.contact_episodes;
  $('activeNeurons').textContent=state.latest.active_neurons.toLocaleString();$('spikes').textContent=state.latest.total_spikes.toLocaleString();
  $('networkSize').textContent=state.metadata.neurons.toLocaleString()+' 个脑模型节点';
  $('bodyState').textContent='朝上 '+h.sense.upright.toFixed(2);$('directory').textContent=h.directory;$('directory').title=h.directory;
  for(const [id,value]of Object.entries({vision:!!h.config.vision_enabled,odor:h.config.odor_enabled,contact:h.config.contact_enabled,taste:h.config.taste_enabled}))$(id).checked=value;
  $('visionRisk').textContent=h.sense.vision?h.sense.vision.risk.map(x=>x.toFixed(2)).join(' / '):'未采样';$('visionTurn').textContent=(h.command.visual_turn||0).toFixed(2)+(h.config.vision_enabled?'':'（视觉关闭）');$('visionTime').textContent=h.sense.vision?h.sense.vision.time_s.toFixed(2)+' s':'—';$('odorValues').textContent=h.sense.odor.map(x=>x.toFixed(4)).join(' / ');
  $('p9Input').textContent=h.command.p9_rate_hz.map(x=>x.toFixed(0)).join(' / ')+' Hz';
  $('p9Actual').textContent=state.latest.p9_hz.map(x=>x.toFixed(0)).join(' / ')+' Hz';
  $('motorDrive').textContent=h.motor_drive.map(x=>x.toFixed(2)).join(' / ');
  $('force').textContent=h.sense.contact_force_un.toFixed(2)+' μN';$('sugar').textContent=state.latest.sugar_spikes+' 次';
  if(h.retina && (h.retina.frame_index??h.retina.time_s)!==lastRetina){lastRetina=h.retina.frame_index??h.retina.time_s;$('retina').src='/api/retina.png?t='+lastRetina;
    $('retinaInfo').textContent=`每眼 ${h.retina.ommatidia_per_eye} 小眼 · 模型 ${h.retina.time_s.toFixed(2)} s · ${h.config.vision_enabled?'驱动视觉避障':'视觉驱动关闭'}`;}
  applyPose(h.pose);drawMap(h);drawRate(state.history);
  trailLine.geometry.dispose();trailLine.geometry=new THREE.BufferGeometry().setFromPoints(h.trail.map(r=>new THREE.Vector3(r[1],r[2],.045)));
  const counts=new Map();for(const [,index]of state.latest.activity?.events||[])counts.set(index,(counts.get(index)||0)+1);
  for(const n of neurons){if(counts.has(n.index))spikeTimes.set(n.index,modelTime);}
  updateSkeletonReadout();
}

function updateSkeletonReadout(){
  const activeIndices=new Set((latestNeuralChunk?.activity?.events||[]).map(e=>e[1]));
  const shown=neurons.filter(n=>n.line.visible),active=shown.filter(n=>activeIndices.has(n.index));
  for(const n of active)spikeTimes.set(n.index,modelTime);
  $('neuronActivityCount').textContent=`本块骨架放电 ${active.length}/${shown.length} · 已下载 ${neurons.length} 个`;
  const activityRows=$('neuronReadout').querySelectorAll('li');
  activityRows.forEach((row,i)=>{
    const label=active[i]?.label||'';
    row.textContent=label;row.title=label;
  });
}

$('retina').onload=()=>{$('retinaWaiting').style.display='none';window.labDiagnostics.retinaImage={width:$('retina').naturalWidth,height:$('retina').naturalHeight};};
window.labDiagnostics={};
loadWorld().catch(e=>{$('sceneMessage').textContent='三维场景加载失败：'+e.message;console.error(e);});
loadBrain().catch(e=>{$('brainInfo').textContent=e.message;$('morphCount').textContent='形态未加载';console.error(e);});

function connect() {
  const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);
  socket.onmessage=event=>{try{render(JSON.parse(event.data));}catch(error){console.error(error);$('message').textContent='显示错误：'+error.message;}};
  socket.onopen=()=>{lastRevision=-1;};
  socket.onclose=()=>{if(!['error','finished','interrupted'].includes(lastStatus))$('status').textContent='● 服务断开，重连中';setTimeout(connect,1500);};
  socket.onerror=()=>socket.close();
}
connect();

function animate() {
  requestAnimationFrame(animate);
  if(worldView) {
    if(following&&latest){
      const p=latest.sense.position_mm,target=new THREE.Vector3(p[0],p[1],p[2]+.3);
      if(!lastTarget){camera.position.copy(target).add(new THREE.Vector3(.8,-6,2.8));worldControls.target.copy(target);}
      else {const delta=target.clone().sub(lastTarget);camera.position.add(delta);worldControls.target.add(delta);}
      lastTarget=target;
    }
    worldControls.update();worldView.render(scene,camera);
  }
  if(brainView) {
    for(const n of neurons){const dt=modelTime-(spikeTimes.get(n.index)??-100);const glow=Math.exp(-dt/.1);
      n.line.material.color.set(glow>.04?'#83f3bf':n.id===selectedNeuronId?'#efb76b':'#bac8d0');n.line.material.opacity=n.id===selectedNeuronId?1:.35+.65*glow;}
    brainControls.update();brainView.render(brainView.scene,brainCamera);
    brainView.scaleBar.style.width=(100/(brainCamera.right-brainCamera.left)*brainCamera.zoom*$('brainCanvas').clientWidth)+'px';
  }
}
animate();
