// All statistics are derived from captured spike events, without changing the brain.
export function makeFilter(catalog, classKey='', query='') {
  const text=query.trim().toLocaleLowerCase(),exact=catalog.types.findIndex(t=>t.key.toLocaleLowerCase()===text);
  const types=new Set(catalog.types.flatMap((t,i)=>!text||(exact>=0?i===exact:t.label.toLocaleLowerCase().includes(text))?[i]:[]));
  const cls=classKey?catalog.classes.findIndex(c=>c.key===classKey):-1;
  const byClass=new Uint32Array(catalog.classes.length),byType=new Uint32Array(catalog.types.length);
  const mask=new Uint8Array(catalog.model_nodes);
  let population=0;
  for(let i=0;i<mask.length;i++) {
    if((!classKey||catalog.class_of[i]===cls)&&types.has(catalog.type_of[i])) {
      mask[i]=1;population++;byClass[catalog.class_of[i]]++;byType[catalog.type_of[i]]++;
    }
  }
  return {mask,byClass,byType,population,classKey,query:query.trim()};
}

export function aggregateWindow(state,catalog,filter,windowMs=1000) {
  const latest=state.latest;
  if(!latest)return null;
  const history=state.history?.length?state.history:[latest];
  const allEvents=Array.isArray(state.events)?state.events:(latest.activity?.events||[]);
  const end=latest.brain_time_ms;
  const earliest=Math.min(...history.map(h=>h.brain_time_ms-h.chunk_ms));
  // Legacy state without a global event buffer only covers its current chunk.
  if(!Number.isFinite(windowMs)||windowMs<1||windowMs>1000)throw Error('显示时间窗须在 1–1000 毫秒内');
  const cutoff=Math.max(0,end-windowMs,earliest,Array.isArray(state.events)?0:end-latest.chunk_ms);
  // Count only whole recorded chunks, so total counts and event denominators agree.
  const eligible=history.filter(h=>h.brain_time_ms-h.chunk_ms>=cutoff-1e-6&&h.brain_time_ms<=end+1e-6);
  const start=eligible.length?Math.min(...eligible.map(h=>h.brain_time_ms-h.chunk_ms)):Math.max(0,end-latest.chunk_ms);
  const durationMs=end-start;if(!(durationMs>0))return null;
  const expectedSpikes=history.filter(h=>h.brain_time_ms-h.chunk_ms>=start-1e-6&&h.brain_time_ms<=end+1e-6)
    .reduce((sum,h)=>sum+h.chunk_spikes,0);
  const classSpikes=new Float64Array(catalog.classes.length),classActive=new Uint32Array(catalog.classes.length);
  const typeSpikes=new Float64Array(catalog.types.length),typeActive=new Uint32Array(catalog.types.length);
  const classBins=catalog.classes.map(()=>new Uint32Array(10)),typeBins=new Map(),neuronSpikes=new Map(),events=[];
  let observedSpikes=0,invalidEvents=0;
  for(const event of allEvents) {
    const [time,index]=event;
    if(time<start-1e-7||time>=end)continue;
    if(!Number.isFinite(time)||!Number.isInteger(index)||index<0||index>=catalog.model_nodes){invalidEvents++;continue;}
    observedSpikes++;
    if(!filter.mask[index])continue;
    const cls=catalog.class_of[index],typ=catalog.type_of[index];
    classSpikes[cls]++;typeSpikes[typ]++;
    neuronSpikes.set(index,(neuronSpikes.get(index)||0)+1);events.push(event);
    const bin=Math.min(9,Math.max(0,Math.floor((time-start)/durationMs*10)));
    classBins[cls][bin]++;
    if(!typeBins.has(typ))typeBins.set(typ,new Uint32Array(10));
    typeBins.get(typ)[bin]++;
  }
  for(const index of neuronSpikes.keys()){classActive[catalog.class_of[index]]++;typeActive[catalog.type_of[index]]++;}
  const classes=catalog.classes.map((group,i)=>({...group,index:i,population:filter.byClass[i],
    spikes:classSpikes[i],active:classActive[i],rateHz:classSpikes[i]*1000/durationMs,
    meanHz:filter.byClass[i]?classSpikes[i]*1000/durationMs/filter.byClass[i]:0}));
  const totalSpikes=events.length;
  return {start,end,durationMs,expectedSpikes,observedSpikes,invalidEvents,
    complete:observedSpikes===expectedSpikes&&invalidEvents===0,
    totalSpikes,active:neuronSpikes.size,population:filter.population,
    activeTypes:typeActive.reduce((n,count)=>n+(count>0),0),classes,typeSpikes,typeActive,
    classBins,typeBins,neuronSpikes,events};
}

export function typeStats(catalog,filter,stats,index) {
  const population=filter.byType[index],spikes=stats.typeSpikes[index];
  return {...catalog.types[index],index,population,spikes,active:stats.typeActive[index],
    rateHz:spikes*1000/stats.durationMs,meanHz:population?spikes*1000/stats.durationMs/population:0};
}

export function curveValues(counts,durationMs,population,mean=true) {
  return Array.from(counts,n=>durationMs>0&&(!mean||population>0)?n*10000/durationMs/(mean?population:1):0);
}

export function typeColor(index) {return `hsl(${(index*137.508)%360} 64% 72%)`;}
