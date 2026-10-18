// Desktop panel layout only. Never sends simulation commands or edits parameter values.
(() => {
  const storeKey='flywire-workbench-v1',windows=new Map();
  let saved={};try{saved=JSON.parse(localStorage.getItem(storeKey)||'{}')||{};}catch{}
  let layer=20,previousFocus=null;
  const defaults={map:[.12,.10,570,640],activity:[.13,.10,850,620],identity:[.17,.13,960,600],
    rate:[.20,.30,620,270],raster:[.13,.20,670,420],classes:[.22,.26,650,440],types:[.30,.32,650,440],about:[.24,.24,540,350],
    'mb-observer':[.12,.08,1000,650]};
  const bounds=()=>({width:innerWidth,height:innerHeight,top:52,bottom:innerHeight-27});
  function persist(){try{localStorage.setItem(storeKey,JSON.stringify(saved));}catch{}}
  function remember(key){const w=windows.get(key);
    saved.windows??={};saved.windows[key]={x:parseFloat(w.element.style.left),y:parseFloat(w.element.style.top),width:parseFloat(w.element.style.width),height:w.height,open:!w.element.hidden,minimized:w.minimized};persist();}
  function constrain(w){const b=bounds(),e=w.element,d=defaults[w.key];
    const minimum=w.key==='mb-observer'?[720,540]:[360,180];
    const width=Math.min(Math.max(minimum[0],parseFloat(e.style.width)||d[2]),b.width-16);
    const height=Math.min(Math.max(minimum[1],w.height||parseFloat(e.style.height)||d[3]),b.bottom-b.top-8);
    const left=Math.max(8,Math.min(parseFloat(e.style.left)||8,b.width-width-8));
    const top=Math.max(b.top,Math.min(parseFloat(e.style.top)||b.top,b.bottom-(w.minimized?34:height)));
    Object.assign(e.style,{width:width+'px',height:w.minimized?'34px':height+'px',left:left+'px',top:top+'px'});w.height=height;
  }
  function raise(w){w.element.style.zIndex=++layer;}
  function syncButtons(){for(const b of document.querySelectorAll('[data-window]')){
    const w=windows.get(b.dataset.window);b.setAttribute('aria-pressed',String(!!w&&!w.element.hidden));b.setAttribute('aria-controls','window-'+b.dataset.window);
  }}
  function show(key,focus=true){const w=windows.get(key);if(!w)return;
    previousFocus=document.activeElement;w.element.hidden=false;
    if(w.minimized)minimize(w,false);constrain(w);raise(w);syncButtons();remember(key);
    if(focus)w.element.querySelector('.wb-window-title').focus({preventScroll:true});
    window.dispatchEvent(new Event('workbench-layout'));
  }
  function hide(w){w.element.hidden=true;syncButtons();remember(w.key);
    const target=document.querySelector(`.wb-window-menu [data-window="${w.key}"]`)||previousFocus;
    if(target?.isConnected)target.focus({preventScroll:true});
  }
  function minimize(w,value=!w.minimized){
    if(value&&!w.element.hidden)w.height=w.element.getBoundingClientRect().height;
    w.minimized=value;w.element.classList.toggle('is-minimized',value);
    const button=w.element.querySelector('[data-minimize]');button.textContent=value?'□':'−';button.title=value?'展开窗口':'折叠窗口';button.setAttribute('aria-expanded',String(!value));
    constrain(w);remember(w.key);window.dispatchEvent(new Event('workbench-layout'));
  }
  function register(element){const key=element.dataset.floating;if(windows.has(key))return;
    const title=element.dataset.title,d=defaults[key],s=saved.windows?.[key];
    const w={key,element,minimized:false,height:s?.height||d[3]};windows.set(key,w);
    const header=document.createElement('div');header.className='wb-window-header';
    header.innerHTML='<button class="wb-window-title" type="button"></button><div class="wb-window-actions"><button type="button" data-minimize title="折叠窗口" aria-expanded="true">−</button><button type="button" data-close title="关闭窗口">×</button></div>';
    const handle=header.querySelector('.wb-window-title');handle.textContent=title;handle.title='拖动移动；双击折叠；方向键微调位置';
    handle.setAttribute('aria-label',title+'，拖动或使用方向键移动');
    element.setAttribute('role','dialog');element.setAttribute('aria-label',title);element.prepend(header);
    Object.assign(element.style,{left:(s?.x??innerWidth*d[0])+'px',top:(s?.y??innerHeight*d[1])+'px',width:(s?.width||d[2])+'px',height:w.height+'px'});
    const grip=document.createElement('button');grip.className='wb-window-resize';grip.title='拖动调整尺寸；方向键调整大小';grip.setAttribute('aria-label','调整'+title+'窗口大小');element.append(grip);
    header.querySelector('[data-close]').onclick=()=>hide(w);header.querySelector('[data-minimize]').onclick=()=>minimize(w);
    handle.ondblclick=()=>minimize(w);element.onpointerdown=()=>raise(w);
    function pointerDrag(control,resize){control.onpointerdown=event=>{
      if(event.button!==0||resize&&w.minimized)return;event.preventDefault();raise(w);
      const r=element.getBoundingClientRect(),sx=event.clientX,sy=event.clientY;control.setPointerCapture(event.pointerId);
      const move=e=>{const dx=e.clientX-sx,dy=e.clientY-sy;
        if(resize){element.style.width=Math.max(360,r.width+dx)+'px';w.height=Math.max(180,r.height+dy);}
        else{element.style.left=r.left+dx+'px';element.style.top=r.top+dy+'px';}
        constrain(w);window.dispatchEvent(new Event('workbench-layout'));
      };
      const end=()=>{control.removeEventListener('pointermove',move);control.removeEventListener('pointerup',end);control.removeEventListener('pointercancel',end);remember(key);};
      control.addEventListener('pointermove',move);control.addEventListener('pointerup',end);control.addEventListener('pointercancel',end);
    };}
    pointerDrag(handle,false);pointerDrag(grip,true);
    for(const [control,resize] of [[handle,false],[grip,true]])control.onkeydown=event=>{
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const delta=event.shiftKey?30:10;
      const dx=event.key==='ArrowRight'?delta:event.key==='ArrowLeft'?-delta:0,dy=event.key==='ArrowDown'?delta:event.key==='ArrowUp'?-delta:0;
      if(resize){element.style.width=element.offsetWidth+dx+'px';w.height+=dy;}
      else{element.style.left=element.offsetLeft+dx+'px';element.style.top=element.offsetTop+dy+'px';}
      constrain(w);remember(key);window.dispatchEvent(new Event('workbench-layout'));
    };
    constrain(w);if(s?.open){element.hidden=false;raise(w);}if(s?.minimized)minimize(w,true);syncButtons();
  }
  function inspector(key,save=true){
    for(const button of document.querySelectorAll('[data-inspector]')){const active=button.dataset.inspector===key;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
    for(const pane of document.querySelectorAll('[data-inspector-pane]'))pane.hidden=pane.dataset.inspectorPane!==key;
    if(save){saved.inspector=key;persist();}window.dispatchEvent(new Event('workbench-layout'));
  }
  for(const e of document.querySelectorAll('[data-floating]'))register(e);
  document.addEventListener('click',event=>{const button=event.target.closest('[data-window]');if(button)show(button.dataset.window);});
  for(const button of document.querySelectorAll('[data-inspector]')){
    button.onclick=()=>inspector(button.dataset.inspector);
    button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=[...document.querySelectorAll('[data-inspector]')],i=tabs.indexOf(button),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();};
  }
  inspector(['brain','senses','behavior','arena','intervention','mb-learning'].includes(saved.inspector)?saved.inspector:'brain',false);
  document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;
    const candidates=[...windows.values()].filter(w=>!w.element.hidden).sort((a,b)=>Number(b.element.style.zIndex)-Number(a.element.style.zIndex));if(candidates[0])hide(candidates[0]);
  });
  document.addEventListener('click',event=>{const link=event.target.closest('a[href^="#"]');if(!link)return;
    const key={neuralActivityPanel:'activity',annotationsPanel:'identity',arena:'map'}[link.hash.slice(1)];if(key){event.preventDefault();show(key);}
  });
  function hash(){const key={neuralActivityPanel:'activity',annotationsPanel:'identity',arena:'map'}[location.hash.slice(1)];if(key)show(key,false);}
  hash();window.addEventListener('hashchange',hash);
  window.addEventListener('resize',()=>{for(const w of windows.values())constrain(w);});
  document.getElementById('wb-reset').onclick=()=>{
    saved={};persist();for(const w of windows.values()){const d=defaults[w.key];w.element.hidden=true;w.minimized=false;w.element.classList.remove('is-minimized');w.height=d[3];Object.assign(w.element.style,{left:innerWidth*d[0]+'px',top:innerHeight*d[1]+'px',width:d[2]+'px'});const button=w.element.querySelector('[data-minimize]');button.textContent='−';button.title='折叠窗口';button.setAttribute('aria-expanded','true');constrain(w);}
    for(const details of document.querySelectorAll('.wb-observer'))details.open=true;
    for(const details of document.querySelectorAll('.bc-group,.bc-transmitters'))details.open=false;
    inspector('brain');syncButtons();document.getElementById('message').textContent='已恢复默认布局；当前实验和参数保持不变。';
  };
  window.Workbench={show,inspector,mountActivity(root){
    for(const [canvas,key,title] of [['na-raster','raster','放电栅格'],['na-class-curve','classes','各大类放电曲线'],['na-type-curve','types','具体类型放电曲线']]){
      const plot=document.getElementById(canvas)?.closest('.na-box');if(!plot)continue;
      const element=document.createElement('section');element.id='window-'+key;element.className='wb-float';element.dataset.floating=key;element.dataset.title=title;element.hidden=true;
      const body=document.createElement('div');body.className='wb-window-body neural-activity wb-plot-body';body.append(plot);element.append(body);document.getElementById('wb-windows').append(element);register(element);
    }
    for(const container of root.querySelectorAll('.na-plots,.na-tables')){
      for(const box of [...container.children]){const details=document.createElement('details');details.className='wb-stat-group';const summary=document.createElement('summary');summary.textContent=box.querySelector('h3')?.textContent||'统计';box.querySelector('h3')?.remove();box.before(details);details.append(summary,box);}
      container.replaceWith(...container.children);
    }
    syncButtons();
  }};
})();
