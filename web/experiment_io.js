/* Local save-as controls. The server owns finalization and copy completion. */
(() => {
  'use strict';
  const make = (tag, text, className) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; };
  const state = {runId: null, busy: false, pending: false, status: null, timer: null, lastTerminal: null, lifecycle: null};
  const dialog = make('dialog', undefined, 'eio-dialog'); dialog.setAttribute('aria-labelledby', 'eio-title');
  const head = make('div', undefined, 'eio-dialog-head'), title = make('h2', '另存实验', ''); title.id = 'eio-title';
  const close = make('button', '×'); close.type = 'button'; close.setAttribute('aria-label', '关闭另存窗口'); head.append(title, close);
  const body = make('div', undefined, 'eio-dialog-body'), explanation = make('p', '', 'eio-caption');
  const directoryBlock = make('div'), label = make('label', '现有目标目录（绝对路径）', 'eio-directory-label'); label.htmlFor = 'eio-directory';
  const row = make('div', undefined, 'eio-directory-row'), input = make('input', undefined, 'eio-directory-input'); input.id = 'eio-directory'; input.type = 'text'; input.placeholder = '例如 D:\\FlyExperiments'; input.autocomplete = 'off'; input.spellcheck = false;
  const browse = make('button', '浏览目录…'); browse.type = 'button'; row.append(input, browse); directoryBlock.append(label, row);
  const status = make('div', '选择一个已有目录，项目会在其中保存独立的实验 ZIP 文件。', 'eio-dialog-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const result = make('div', undefined, 'eio-dialog-result'); result.hidden = true;
  body.append(explanation, directoryBlock, make('p', '保留项目默认记录；另存的 ZIP 包含数据、配置和保存结果，不包含程序环境或大脑模型资源。已有实验不会被覆盖。', 'eio-caption'), status, result);
  const footer = make('div', undefined, 'eio-dialog-footer'), cancel = make('button', '关闭'), submit = make('button', '结束并另存', 'eio-primary'); cancel.type = submit.type = 'button'; footer.append(cancel, submit); dialog.append(head, body, footer); document.body.append(dialog);
  const toast = make('aside', undefined, 'eio-toast'); toast.hidden = true; toast.setAttribute('role', 'status'); toast.setAttribute('aria-live', 'polite'); document.body.append(toast);
  async function request(url, payload) {
    const response = await fetch(url, payload === undefined ? {cache: 'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload), cache:'no-store'});
    let data; try { data = await response.json(); } catch (_) { throw new Error(`无法读取服务响应（HTTP ${response.status}）`); }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data.error || data.message || `HTTP ${response.status}`));
    return data;
  }
  const recordsUrl = id => id ? `/records?run=${encodeURIComponent(String(id))}` : '/records';
  function setStatus(text, kind = '') { status.textContent = text; status.className = `eio-dialog-status ${kind}`; }
  function setBusy(busy) { state.busy = busy; input.disabled = browse.disabled = submit.disabled = busy || state.pending; updateButton(); }
  function updateButton() { const button = document.getElementById('saveAs'); if (button) button.disabled = state.busy || state.pending || state.lifecycle !== 'running'; }
  function resultLinks(id, retry = false) {
    result.replaceChildren(); const link = make('a', retry ? '打开本轮记录，重试另存' : '查看本轮保存记录'); link.href = recordsUrl(id); result.append(link); result.hidden = false;
  }
  function notify(text, id, error = false) {
    toast.replaceChildren(); const dismiss = make('button', '×'); dismiss.type = 'button'; dismiss.setAttribute('aria-label','关闭另存提示'); dismiss.addEventListener('click', () => {toast.hidden = true;});
    toast.append(dismiss, make('p', text, error ? 'eio-error' : 'eio-saved')); const link = make('a', error ? '打开记录重试另存' : '查看保存记录'); link.href = recordsUrl(id); toast.append(link); toast.hidden = false;
  }
  function schedule() { clearTimeout(state.timer); state.timer = setTimeout(pollStatus, state.pending ? 1500 : 5000); }
  async function pollStatus() {
    try {
      const saved = await request('/api/experiments/save-status'); state.status = saved;
      state.pending = ['pending','saving'].includes(saved.status);
      if (state.pending) {
        if (!state.runId) setStatus(`${saved.status === 'pending' ? '已提交。正在结束本轮并保存默认记录…' : '正在收尾并另存实验，请等待服务报告结果…'}\n目标目录：${saved.destination_directory || '等待目录信息'}`);
      } else if (['saved','error'].includes(saved.status)) {
        const identity = JSON.stringify([saved.status,saved.run_id,saved.path,saved.error]);
        const text = saved.status === 'saved' ? `另存完成：${saved.path || saved.destination_directory || '已完成（服务未返回具体路径）'}` : `另存失败：${typeof saved.error === 'string' ? saved.error : JSON.stringify(saved.error || '请查看本轮记录与日志')}\n默认记录是否完整，请到实验记录页确认。`;
        if (!state.runId) {setStatus(text, saved.status === 'saved' ? 'eio-saved' : 'eio-error'); resultLinks(saved.run_id, saved.status === 'error');}
        if (identity !== state.lastTerminal) {state.lastTerminal = identity; notify(text, saved.run_id, saved.status === 'error');}
      }
      setBusy(state.busy);
    } catch (error) {
      if (state.pending) {setStatus(`暂时无法确认另存结果：${error.message}\n不要重复提交。服务端仍负责收尾，可稍后刷新实验记录核对。`, 'eio-error'); resultLinks(state.status?.run_id, true);}
    }
    if (state.pending || document.getElementById('saveAs') || dialog.open) schedule();
  }
  function openSaveAs(options = {}) {
    if (state.busy) return;
    state.runId = options.runId ? String(options.runId) : null; result.hidden = true; title.textContent = state.runId ? '另存已保存实验' : '结束本轮并另存';
    explanation.textContent = state.runId ? `实验 ${state.runId}：将已保存数据与配置打包为独立 ZIP，保存到选择的目录，不启动或停止仿真。` : '此操作会结束当前实验，先完成默认记录的收尾保存，再将实验 ZIP 另存到指定目录。页面关闭不会取消服务端保存。';
    submit.textContent = state.runId ? '另存到该目录' : '结束并另存';
    setStatus(state.pending ? '当前已有另存任务，正在等待服务端完成。' : '选择一个已有目录，项目会在其中保存独立的实验 ZIP 文件。');
    setBusy(false); if (!dialog.open) dialog.showModal(); if (!state.pending) input.focus(); pollStatus();
  }
  browse.addEventListener('click', async () => {
    if (state.busy || state.pending) return; setBusy(true); setStatus('请在系统目录选择窗口中选择现有目录…');
    try {const choice = await request('/api/experiments/choose-directory', {}); if (choice.directory && !choice.cancelled) {input.value = choice.directory; setStatus('已选择目录，请点击下方按钮提交。');} else setStatus('已取消选择目录；可以手动输入绝对路径。');}
    catch(error) {setStatus(`无法打开目录选择：${error.message}\n也可手动输入现有目录的绝对路径。`, 'eio-error');}
    finally {setBusy(false);}
  });
  submit.addEventListener('click', async () => {
    if (state.busy || state.pending) return; const directory = input.value.trim(); if (!directory) {setStatus('请先选择或输入一个现有目录的绝对路径。','eio-error');input.focus();return;}
    const runId = state.runId; setBusy(true); result.hidden = true; setStatus(runId ? '正在另存已保存记录…' : '正在提交结束并另存请求…');
    try {
      const response = await request(runId ? `/api/experiments/${encodeURIComponent(runId)}/save-to` : '/api/experiments/save-current', {destination_directory:directory});
      if (runId) {const text=`另存完成：${response.path || directory}`;setStatus(text,'eio-saved');resultLinks(response.run_id || runId);notify(text,response.run_id || runId);}
      else {state.pending=true;state.status={status:'pending',run_id:response.run_id,destination_directory:directory};setStatus(response.message || '已受理。正在结束本轮、保存并另存…');resultLinks(response.run_id);await pollStatus();}
    } catch(error) {setStatus(`提交或另存失败：${error.message}`, 'eio-error');resultLinks(runId || state.status?.run_id,true);}
    finally {setBusy(false);}
  });
  for (const button of [close,cancel]) button.addEventListener('click',()=>dialog.close());
  window.ExperimentIO = {openSaveAs, updateState(value) {state.lifecycle=value.status;updateButton();}};
  document.getElementById('saveAs')?.addEventListener('click',()=>openSaveAs());
  pollStatus();
})();
