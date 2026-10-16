import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {makeFilter,aggregateWindow,curveValues} from './web/neural_activity_core.js';

const catalog={model_nodes:5,classes:[{key:'central',population:2},{key:'sensory',population:2},{key:'__unknown_class__',population:1}],
  types:[{key:'A',label:'A',population:2},{key:'B',label:'B',population:1},{key:'AB',label:'AB',population:1},{key:'__unknown_type__',label:'类型未知',population:1}],
  class_of:[0,0,1,1,2],type_of:[0,1,0,2,3]};
const state={latest:{brain_time_ms:2000,chunk_ms:1000,chunk_spikes:4},
  history:[{brain_time_ms:1000,chunk_ms:1000,chunk_spikes:1},{brain_time_ms:2000,chunk_ms:1000,chunk_spikes:4}],
  events:[[999.9,3],[1000,0],[1100,0],[1500,2],[1999.9,4],[2000,1]]};
const filter=makeFilter(catalog),stats=aggregateWindow(state,catalog,filter);
assert.equal(stats.complete,true);assert.equal(stats.totalSpikes,4);assert.equal(stats.active,3);assert.equal(stats.activeTypes,2);
assert.equal(stats.start,1000);assert.equal(stats.durationMs,1000);
assert.deepEqual(stats.classes.map(g=>g.spikes),[2,1,1]);
assert.deepEqual(stats.classes.map(g=>g.active),[1,1,1]);
assert.equal(stats.classes[0].meanHz,1); // includes the silent central neuron in its denominator
assert.equal(stats.typeSpikes[3],1);assert.equal(stats.typeActive[3],1);
assert.equal(stats.classes.reduce((n,g)=>n+g.spikes,0),stats.totalSpikes);
assert.equal(stats.typeSpikes.reduce((a,b)=>a+b,0),stats.totalSpikes);
assert.equal(stats.classBins[0][0],1);assert.equal(stats.classBins[0][1],1);assert.equal(stats.classBins[2][9],1);
assert.equal(makeFilter(catalog,'','A').population,2); // exact type must not include AB
const selected=makeFilter(catalog,'sensory','A'),subset=aggregateWindow(state,catalog,selected);
assert.equal(selected.population,1);assert.equal(subset.totalSpikes,1);assert.equal(subset.classes[1].meanHz,1);
assert.equal(makeFilter(catalog,'','__unknown_type__').population,1);
assert.equal(makeFilter(catalog,'does-not-exist').population,0);
const none=aggregateWindow(state,catalog,makeFilter(catalog,'','no-such-type'));
assert.equal(none.totalSpikes,0);assert.equal(none.population,0);assert.ok(none.classes.every(g=>g.meanHz===0));
assert.deepEqual(curveValues(new Uint32Array(10),1000,0),Array(10).fill(0));
assert.equal(curveValues(new Uint32Array([2,0,0,0,0,0,0,0,0,0]),1000,2)[0],10);
const capped=structuredClone(state);capped.events=capped.events.slice(2);
assert.equal(aggregateWindow(capped,catalog,filter).complete,false);
const silent={latest:{brain_time_ms:20,chunk_ms:20,chunk_spikes:0},history:[{brain_time_ms:20,chunk_ms:20,chunk_spikes:0}],events:[]};
assert.equal(aggregateWindow(silent,catalog,filter).complete,true);
const chunkOnly={latest:{brain_time_ms:2000,chunk_ms:20,chunk_spikes:1,activity:{events:[[1999.9,4]]}}};
assert.equal(aggregateWindow(chunkOnly,catalog,filter).durationMs,20);
const displayHistory=Array.from({length:50},(_,i)=>({brain_time_ms:(i+1)*20,chunk_ms:20,chunk_spikes:1}));
const displayState={latest:displayHistory.at(-1),history:displayHistory,events:displayHistory.map(h=>[h.brain_time_ms-10,0])};
for(const windowMs of [100,200,500,1000]){
  const window=aggregateWindow(displayState,catalog,filter,windowMs);
  assert.equal(window.complete,true);assert.equal(window.durationMs,windowMs);
  assert.equal(window.totalSpikes,windowMs/20);assert.equal(window.classes[0].meanHz,25);
}
const partial={latest:{brain_time_ms:1030,chunk_ms:10,chunk_spikes:1},
  history:[...displayHistory,{brain_time_ms:1020,chunk_ms:20,chunk_spikes:1},{brain_time_ms:1030,chunk_ms:10,chunk_spikes:1}],
  events:[...displayState.events,[1010,0],[1025,0]]};
const aligned=aggregateWindow(partial,catalog,filter,100);
assert.equal(aligned.start,940);assert.equal(aligned.durationMs,90);assert.equal(aligned.complete,true);

const full=JSON.parse(readFileSync(new URL('./web/activity_catalog.json',import.meta.url),'utf8'));
assert.equal(full.model_nodes,138639);assert.equal(full.types.length,8846);
assert.equal(full.classes.reduce((n,g)=>n+g.population,0),full.model_nodes);
assert.equal(full.types.reduce((n,g)=>n+g.population,0),full.model_nodes);
assert.equal(full.classes.at(-1).population,14);assert.equal(full.types.at(-1).population,1492);
assert.ok(full.ids.every(id=>typeof id==='string'&&id.length===18));
const apiBase=process.argv[2]||process.env.FLYWIRE_API_URL||'http://127.0.0.1:8472';
const actual=await(await fetch(apiBase.replace(/\/$/,'')+'/api/state')).json();
const actualStats=aggregateWindow(actual,full,makeFilter(full));
assert.equal(actualStats.classes.reduce((n,g)=>n+g.spikes,0),actualStats.totalSpikes);
assert.equal(actualStats.typeSpikes.reduce((a,b)=>a+b,0),actualStats.totalSpikes);
assert.equal(actualStats.population,138639);
const p9=full.ids.indexOf('720575940627652358');
assert.equal(full.types[full.type_of[p9]].key,'DNp09');assert.equal(full.classes[full.class_of[p9]].key,'descending');
const out={synthetic_checks:'passed',full_catalog:'passed',model_nodes:138639,named_types:8845,unknown_types:1492,
  actual_run:{status:actual.status,model_time_ms:actualStats.end,window_ms:actualStats.durationMs,
    observed_events:actualStats.observedSpikes,expected_events:actualStats.expectedSpikes,complete:actualStats.complete,
    class_sum:actualStats.classes.reduce((n,g)=>n+g.spikes,0),type_sum:actualStats.typeSpikes.reduce((a,b)=>a+b,0)}};
mkdirSync(new URL('./output/activity/',import.meta.url),{recursive:true});
writeFileSync(new URL('./output/activity/group_verification.json',import.meta.url),JSON.stringify(out,null,2));
console.log(JSON.stringify(out,null,2));
