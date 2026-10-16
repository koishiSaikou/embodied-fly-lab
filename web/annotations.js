/* IDs stay strings; annotation data is rendered only as text. */
(() => {
  const $=id=>document.getElementById(id),root=$('annotationsPanel');
  const side=v=>({left:'左',right:'右',center:'中线'}[v]||'未知');
  const nt=v=>({acetylcholine:'乙酰胆碱',glutamate:'谷氨酸',gaba:'GABA',dopamine:'多巴胺',
    serotonin:'血清素',octopamine:'章鱼胺',histamine:'组胺'}[v]||v||'未知');
  const prediction=a=>a?.top_nt?nt(a.top_nt)+(a.top_nt_conf==null?'':` (${(a.top_nt_conf*100).toFixed(1)}%)`):'未知';
  const values=n=>[n.index,n.flywire_id,n.annotation?.cell_type||'类型未知',side(n.annotation?.side),prediction(n.annotation),n.spikes,n.hz.toFixed(0)];
  let live;
  window.NeuronAnnotations={activityValues:values,updateActivity(rows=[]){
    if(!live)return;live.replaceChildren();
    for(const n of rows.slice(0,8)){const tr=live.insertRow();for(const v of values(n))tr.insertCell().textContent=v;}
    if(!rows.length){const td=live.insertRow().insertCell();td.colSpan=7;td.textContent='当前块无放电，或尚未收到新实验数据。';}
  }};
  if(!root)return;
  root.innerHTML=`<div class="panel-head"><h2>神经元身份与类型</h2><span class="tag" id="annotationVersion">读取注释…</span></div>
    <p class="caption" id="annotationCoverage" role="status"></p>
    <form id="annotationForm" class="annotation-form">
      <label>类型 / hemibrain 类型 / ID <input id="annotationQuery" maxlength="120" placeholder="例如 DNp09、LB4b、KCg-m" autocomplete="off"></label>
      <label>大类 <select id="annotationClass"><option value="">全部大类</option></select></label>
      <label>预测递质 <select id="annotationNt"><option value="">全部递质</option></select></label>
      <label class="annotation-check"><input type="checkbox" id="annotationUnknown">仅类型未知</label><button type="submit">查询</button>
    </form>
    <div class="caption" id="annotationResults" role="status"></div>
    <div class="annotation-scroll"><table class="annotation-table"><thead><tr><th>具体类型</th><th>FlyWire ID</th><th>大类 / 类</th><th>解剖侧别</th><th>预测递质 / 置信度</th><th>文献递质注释</th></tr></thead><tbody id="annotationRows"></tbody></table></div>
    <div class="annotation-pages"><button id="annotationPrev" type="button">上一页</button><button id="annotationNext" type="button">下一页</button><span class="caption">每页 20 个 · 点击一行查看来源；已有骨架可在三维脑图定位</span></div>
    <p class="annotation-detail caption" id="annotationDetail" aria-live="polite">请选一个神经元。侧别指脑内神经元的胞体侧，感觉/上行神经元的神经入口侧；类名不等于精确脑区。</p>
    <p class="caption">P9 通道 A / B 沿用原控制顺序；注释的解剖侧别分别为右 / 左，上游左右命名存在差异。导入类型暂不改变 LIF 方程、权重或行为规则。</p>
    <p class="caption"><a id="annotationSource" target="_blank" rel="noopener">官方注释来源</a> · <a href="/api/annotations/report" target="_blank" rel="noopener">覆盖率与校验报告</a> · <a href="/api/annotations/unknown.csv">下载类型未知清单</a></p>
    <details class="annotation-live"><summary>当前块最活跃神经元（带类型）</summary><div class="annotation-scroll"><table class="annotation-table"><thead><tr><th>模型编号</th><th>FlyWire ID</th><th>类型</th><th>解剖侧别</th><th>预测递质</th><th>次数</th><th>Hz</th></tr></thead><tbody id="annotationLive"></tbody></table></div></details>`;
  live=$('annotationLive');
  let offset=0,total=0,serial=0,controller;
  async function search(){
    const current=++serial;controller?.abort();controller=new AbortController();
    const params=new URLSearchParams({q:$('annotationQuery').value,super_class:$('annotationClass').value,
      nt:$('annotationNt').value,unknown_only:$('annotationUnknown').checked,limit:20,offset});
    $('annotationResults').textContent='查询中…';
    try{
      const res=await fetch('/api/annotations/search?'+params,{signal:controller.signal});
      if(!res.ok)throw Error('查询失败 '+res.status);
      const data=await res.json();if(current!==serial)return;
      if(!data.available)throw Error('注释尚未导入');
      total=data.total;$('annotationRows').replaceChildren();
      for(const row of data.rows){
        const tr=$('annotationRows').insertRow();tr.tabIndex=0;tr.setAttribute('aria-label',`${row.cell_type||'类型未知'} ${row.flywire_id}，查看详情`);
        const type=(row.cell_type||'类型未知')+(row.hemibrain_type&&row.hemibrain_type!==row.cell_type?` (hemibrain: ${row.hemibrain_type})`:'');
        for(const v of [type,row.flywire_id,[row.super_class,row.cell_class].filter(Boolean).join(' / ')||'未知',
          side(row.side),prediction(row),row.known_nt||'未提供'])tr.insertCell().textContent=v;
        const select=()=>{
          for(const r of $('annotationRows').rows)r.classList.toggle('selected',r===tr);
          $('annotationDetail').textContent=[`ID ${row.flywire_id} · 模型编号 ${row.index}`,
            `类型 ${row.cell_type||'未知'}；hemibrain 类型 ${row.hemibrain_type||'未提供'}`,
            `分类 ${[row.super_class,row.cell_class,row.cell_sub_class].filter(Boolean).join(' → ')||'未知'}`,
            `预测递质 ${prediction(row)}；文献注释 ${row.known_nt||'未提供'}`,
            `文献来源 ${row.known_nt_source||'未提供'}；别名 ${row.synonyms||'未提供'}`,
            row.annotation_status==='unmatched'?'该 ID 在模型中，但注释表没有匹配行。':'注释行已匹配。'].join('\n');
          const event=new CustomEvent('annotation-select',{detail:{row,note:''}});window.dispatchEvent(event);
          if(event.detail.note)$('annotationDetail').textContent+='\n'+event.detail.note;
        };
        tr.onclick=select;tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select();}};
      }
      if(!total){const td=$('annotationRows').insertRow().insertCell();td.colSpan=6;td.textContent='没有符合条件的模型节点。';}
      $('annotationResults').textContent=`共 ${total.toLocaleString()} 个 · ${total?offset+1:0}–${Math.min(offset+20,total)}`;
      $('annotationPrev').disabled=offset===0;$('annotationNext').disabled=offset+20>=total;
    }catch(e){if(e.name!=='AbortError')$('annotationResults').textContent=e.message;}
  }
  $('annotationForm').onsubmit=e=>{e.preventDefault();offset=0;search();};
  for(const id of ['annotationClass','annotationNt','annotationUnknown'])$(id).onchange=()=>{offset=0;search();};
  $('annotationPrev').onclick=()=>{offset=Math.max(0,offset-20);search();};
  $('annotationNext').onclick=()=>{if(offset+20<total){offset+=20;search();}};
  (async()=>{
    try{
      const res=await fetch('/api/annotations/summary');if(!res.ok)throw Error('服务尚未支持注释，请刷新更新后的服务');
      const s=await res.json();if(!s.available)throw Error(s.reason);
      $('annotationVersion').textContent=`v${s.materialization} · 注释 ${s.annotation_version}`;
      const fmt=x=>x.toLocaleString('zh-CN');
      $('annotationCoverage').textContent=`模型 ${fmt(s.model_nodes)} 个节点 · 匹配注释 ${fmt(s.matched_rows)} 个 · 具体类型 ${fmt(s.typed_nodes)} 个（${(s.typed_nodes/s.model_nodes*100).toFixed(2)}%）· 类型未知 ${fmt(s.unknown_type_nodes)} 个（含 ${s.unmatched_rows} 个未匹配 ID）`;
      for(const name of s.super_classes)$('annotationClass').add(new Option(name,name));
      for(const name of s.predicted_transmitters)$('annotationNt').add(new Option(nt(name),name));
      $('annotationSource').href=s.source_url;await search();
    }catch(e){$('annotationCoverage').textContent='注释不可用：'+e.message;$('annotationVersion').textContent='未载入';}
  })();
})();
