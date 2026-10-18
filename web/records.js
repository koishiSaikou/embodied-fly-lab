/* Saved records are summaries and reproducible inputs, not neural checkpoints. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = {runs: [], selected: null, detail: null, capability: null, tab: 'overview', detailRequest: 0, previewRequest: 0, chartObserver: null, busy: false};
  const tabs = [['overview', '实验概览'], ['trajectory', '运动轨迹'], ['neural', '神经块汇总'], ['mb_learning', '蘑菇体学习'], ['behavior', '行为与学习'], ['senses', '感官数据'], ['retina', '复眼与录像'], ['events', '调整事件'], ['config', '初末配置'], ['files', '全部文件']];
  const statusNames = {finished: '已结束', complete: '已完成', completed: '已完成', error: '异常结束', running: '运行中', recording: '记录中', incomplete: '记录不完整', unreadable: '记录无法读取', saving: '收尾保存中', stopped: '已停止', interrupted: '已中断', imported: '已导入', unknown: '未记录状态'};
  function node(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; }
  function present(value) { return value !== undefined && value !== null && value !== ''; }
  function first(...values) { return values.find(present); }
  function display(value) { if (!present(value)) return '—'; return typeof value === 'object' ? JSON.stringify(value) : String(value); }
  function finite(value) { if (!present(value)) return null; const n = Number(value); return Number.isFinite(n) ? n : null; }
  function seconds(value) { const n = finite(value); return n === null ? '—' : `${n.toLocaleString('zh-CN', {maximumFractionDigits: 3})} 秒`; }
  function bytes(value) { const n = finite(value); if (n === null) return '大小未记录'; if (n < 1024) return `${n} B`; if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`; return `${(n / 1048576).toFixed(1)} MB`; }
  function message(text, type = '') { $('message').textContent = text; $('message').className = `message ${type}`; }
  function apiPath(id, suffix = '') { return `/api/experiments/${encodeURIComponent(String(id))}${suffix}`; }
  function filePath(name) { return apiPath(state.selected, `/file/${encodeURIComponent(name)}`); }
  async function api(url, options = {}) {
    const response = await fetch(url, {...options, cache: 'no-store'});
    let body; try { body = await response.json(); } catch (_) { throw new Error(`服务返回无法读取的内容（HTTP ${response.status}）`); }
    if (!response.ok) throw new Error(display(first(body.detail, body.error, body.message, `HTTP ${response.status}`)));
    return body;
  }
  function files(detail = state.detail) { return (detail?.files || []).map(f => typeof f === 'string' ? {name: f} : f).filter(f => typeof f.name === 'string'); }
  function pretty(value) { const text = JSON.stringify(value ?? {}, null, 2); return text.length > 160000 ? `${text.slice(0, 160000)}\n…显示已截断，请下载原始文件查看全文。` : text; }
  function jsonBlock(value) { return node('pre', pretty(value), 'json'); }
  function section(title) { const box = node('section', undefined, 'section'); box.append(node('h3', title)); return box; }
  function disclosure(title, value, open = false) { const box = node('details'); box.open = open; box.append(node('summary', title), jsonBlock(value)); return box; }
  function note(text, warning = false) { return node('p', text, `note${warning ? ' warning' : ''}`); }
  function download(name, label = name) { const a = node('a', label); a.href = filePath(name); a.download = ''; return a; }
  function summaryParts(run) {
    const report = run.report || {}, config = run.config || {};
    return {status: first(run.status, report.status, 'unknown'), duration: first(run.time_s, run.body_model_seconds, run.duration_s, run.model_seconds, report.body_model_seconds), seed: first(run.seed, report.seed, config.arguments?.seed), version: first(run.version, run.project?.version, report.project?.version, config.project?.version)};
  }
  function renderList() {
    const query = $('search').value.trim().toLowerCase(); $('runList').replaceChildren();
    const filtered = state.runs.filter(r => `${display(r.id)} ${display(r.name)} ${display(r.label)}`.toLowerCase().includes(query));
    $('runCount').textContent = `(${filtered.length}/${state.runs.length})`;
    if (!filtered.length) { $('runList').append(node('p', state.runs.length ? '没有匹配的实验。' : '暂无记录。实验结束保存后可刷新，或导入朋友分享的 ZIP。', 'empty-inline')); return; }
    for (const run of filtered) {
      const info = summaryParts(run), button = node('button', undefined, `run-item${String(run.id) === state.selected ? ' active' : ''}`);
      button.type = 'button'; button.setAttribute('role', 'listitem'); button.setAttribute('aria-current', String(run.id) === state.selected ? 'true' : 'false');
      button.append(node('span', display(first(run.name, run.label, run.id)), 'run-name'));
      const meta = node('span', undefined, 'run-meta'); meta.append(node('span', statusNames[info.status] || display(info.status)), node('span', seconds(info.duration)));
      if (present(info.seed)) meta.append(node('span', `种子 ${info.seed}`));
      button.append(meta); button.addEventListener('click', () => selectRun(String(run.id))); $('runList').append(button);
    }
  }
  async function refreshList(selectId = null) {
    $('refresh').disabled = true;
    try {
      const result = await api('/api/experiments'); state.runs = Array.isArray(result.runs) ? result.runs : []; renderList();
      if (selectId && state.runs.some(r => String(r.id) === String(selectId))) await selectRun(String(selectId));
      else if (state.selected && state.runs.some(r => String(r.id) === state.selected)) await selectRun(state.selected);
      else if (state.runs.length) await selectRun(String(state.runs[0].id));
      else {
        ++state.detailRequest; ++state.previewRequest; state.selected = null; state.detail = null; clearChart();
        $('record').hidden = true; $('empty').hidden = false; renderList(); renderReproduce(); message('暂无本地实验记录。');
      }
    } catch (error) { message(`读取记录失败：${error.message}`, 'error'); }
    finally { $('refresh').disabled = false; }
  }
  async function capabilities() {
    try { state.capability = await api('/api/experiments/capabilities'); }
    catch (_) { state.capability = {can_reproduce: false, reason: '无法确认当前服务是否能安全启动实验，请刷新后重试。'}; }
    renderReproduce();
  }
  async function selectRun(id) {
    if (state.busy) { message('操作正在进行，请稍后再切换实验。'); return; }
    const request = ++state.detailRequest; ++state.previewRequest; state.selected = id; state.detail = null; clearChart(); renderList(); $('record').hidden = true; $('empty').hidden = false; message('正在读取实验详情…');
    try {
      const detail = await api(apiPath(id)); if (request !== state.detailRequest) return;
      state.detail = detail; $('record').hidden = false; $('empty').hidden = true;
      const url = new URL(window.location.href); url.searchParams.set('run', id); window.history.replaceState(null, '', url);
      $('recordTitle').textContent = display(first(detail.name, detail.label, detail.id, id));
      $('recordIdentity').textContent = `记录编号：${id}${present(detail.created_at) ? ` · ${display(detail.created_at)}` : ''}`;
      const canExport = detail.can_export === true && !detail.active && detail.has_report === true;
      if (canExport) $('exportLink').href = apiPath(id, '/export'); else $('exportLink').removeAttribute('href');
      $('exportLink').setAttribute('aria-disabled', String(!canExport)); $('exportLink').tabIndex = canExport ? 0 : -1;
      $('exportLink').style.opacity = canExport ? '' : '.45'; $('exportLink').style.cursor = canExport ? '' : 'default';
      $('exportLink').title = canExport ? '下载本轮实验数据与配置' : detail.active ? '本轮仍在记录，请结束并完成保存后再导出' : '缺少完成报告，暂不能导出分享包';
      $('recordSaveAs').disabled = !canExport; $('recordSaveAs').title = canExport ? '把这份实验另存到指定本地目录' : $('exportLink').title;
      renderMetrics(detail); renderReproduce(); renderTabs(); await renderTab();
      if (request === state.detailRequest) message('已打开本地记录。预览可能经过抽样，完整数据请下载原始文件。');
    } catch (error) { if (request === state.detailRequest) message(`打开记录失败：${error.message}`, 'error'); }
  }
  function renderMetrics(detail) {
    const info = summaryParts(detail); $('metrics').replaceChildren();
    for (const [label, value] of [['状态', statusNames[info.status] || display(info.status)], ['模型时长', seconds(info.duration)], ['随机种子', display(info.seed)], ['程序版本', display(info.version)], ['保存文件', `${files(detail).length} 项`]]) {
      const box = node('div', undefined, 'metric'); box.append(node('div', label, 'metric-label'), node('div', value, 'metric-value')); $('metrics').append(box);
    }
  }
  function renderReproduce() {
    const detail = state.detail, cap = state.capability;
    const sourceReady = Boolean(detail && !detail.active && detail.has_report === true);
    const allowed = Boolean(cap?.can_reproduce && sourceReady && !state.busy && detail.can_reproduce !== false);
    $('reproduceInitial').disabled = !allowed; $('reproduceFinal').disabled = !allowed; $('replayEvents').disabled = !allowed;
    const sourceReason = detail?.active ? '这份记录仍在写入，请结束并完成保存后再用作复现起点。' : detail && !detail.has_report ? '这份记录缺少完成报告，暂不能用作复现起点。' : null;
    $('reproduceReason').textContent = state.busy ? '正在处理，请稍候…' : display(first(sourceReason, detail?.reproduce_reason, cap?.reason, allowed ? '当前服务允许新开实验。缺失文件、版本或资源差异会在启动前提示。' : '当前不能直接新开实验，请先结束现有实验并等待保存完成。'));
    $('cliBox').hidden = !(sourceReady && cap && !cap.can_reproduce && (cap.standalone || cap.mode === 'viewer' || cap.can_run === false || cap.viewer_only));
    // Identifiers accepted by the server are directory names, never executable inputs.
    if (detail) {
      const safeId = String(state.selected).replace(/[^a-zA-Z0-9_.-]/g, '_');
      $('cliCommand').value = first(cap?.reproduce_command, detail.reproduce_command, `.\\Start-Habitat.cmd --experiment output/habitat/${safeId}`);
    }
  }
  async function reproduce(mode) {
    if (state.busy || !state.capability?.can_reproduce || !state.detail || state.detail.active || state.detail.has_report !== true || state.detail.can_reproduce === false) return;
    const recordId = state.selected; state.busy = true; renderReproduce(); message('正在核对复现条件并请求新开实验…');
    try {
      const result = await api(apiPath(recordId, '/reproduce'), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode, replay_events: mode === 'initial' && $('replayEvents').checked})});
      message(display(first(result.message, '新实验已受理。请返回实验场查看加载与运行状态；当前记录仍然保留。')), 'success');
      if (Array.isArray(result.warnings) && result.warnings.length) message(`${$('message').textContent}\n兼容性与复现提示：\n${result.warnings.map(display).join('\n')}`, 'success');
      $('reproduceReason').textContent = '新实验已受理。点击顶部“返回实验场”查看；再次新开须等待该轮结束并保存。';
      state.capability = {can_reproduce: false, reason: $('reproduceReason').textContent};
    } catch (error) { message(`未能新开实验：${error.message}`, 'error'); await capabilities(); }
    finally { state.busy = false; renderReproduce(); }
  }
  function renderTabs() {
    $('tabs').replaceChildren();
    for (const [key, label] of tabs) {
      const button = node('button', label); button.type = 'button'; button.id = `tab-${key}`; button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', 'content'); button.setAttribute('aria-selected', String(state.tab === key)); button.tabIndex = state.tab === key ? 0 : -1;
      button.addEventListener('click', () => { state.tab = key; renderTabs(); renderTab(); });
      button.addEventListener('keydown', event => { const index = tabs.findIndex(t => t[0] === key); let next = index; if (event.key === 'ArrowRight') next = (index + 1) % tabs.length; else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length; else if (event.key === 'Home') next = 0; else if (event.key === 'End') next = tabs.length - 1; else return; event.preventDefault(); state.tab = tabs[next][0]; renderTabs(); $(`tab-${state.tab}`).focus(); renderTab(); });
      $('tabs').append(button);
    }
  }
  function clearChart() { if (state.chartObserver) state.chartObserver.disconnect(); state.chartObserver = null; }
  function showOverview() {
    const d = state.detail, report = d.report || {}; const box = section('保存范围');
    box.append(note('本页查看已保存的数据、初末配置与外置学习记忆。复现会从初始身体状态重新运行；保存包没有完整脑积分状态、全程逐神经元放电或中途身体检查点。'));
    const warnings = [...(Array.isArray(d.warnings) ? d.warnings : []), ...(Array.isArray(d.missing_files) ? d.missing_files.map(f => `缺少文件：${display(f)}`) : [])];
    if (report.failure) warnings.push(`实验异常：${display(report.failure)}`);
    if (Array.isArray(report.cleanup_errors) && report.cleanup_errors.length) warnings.push(`保存或收尾错误：${display(report.cleanup_errors)}`);
    if (warnings.length) box.append(note(warnings.map(display).join('\n'), true));
    const grid = node('div', undefined, 'summary-grid'), metrics = section('实验结果'), identity = section('版本与来源');
    metrics.append(jsonBlock(report.metrics || report)); identity.append(jsonBlock(first(report.project, d.config?.project, d.project, {}))); grid.append(metrics, identity); $('content').append(box, grid);
    const raw = section('完整报告与保存状态'); raw.append(disclosure('查看报告 JSON', report), disclosure('查看记录元信息', Object.fromEntries(Object.entries(d).filter(([key]) => !['config', 'report', 'snapshot'].includes(key))))); $('content').append(raw);
  }
  function showConfig() {
    const d = state.detail, config = d.config || {}, report = d.report || {}, snapshot = d.snapshot || {}, habitat = snapshot.habitat || {};
    const initial = section('初始配置'); initial.append(disclosure('场景、感官与行为参数', first(config.environment, config.arena, {}), true), disclosure('脑刺激、递质与反馈参数', config.brain_controls || {}), disclosure('初始神经干预协议与目标', {protocol:config.neural_intervention??null,targets:config.neural_intervention_targets??null}), disclosure('启动参数与随机种子', config.arguments || {}));
    const final = section('结束时的配置与外置记忆'); final.append(disclosure('最终场景与行为控制', first(report.arena_final, habitat.config, {}), true), disclosure('最终脑参数', first(report.brain_controls_final, habitat.brain_controls, {})), disclosure('最终神经干预协议、实际作用与目标', {protocol:report.neural_intervention_final??null,actual:report.neural_intervention_actual??null,targets:report.neural_intervention_targets??null}), disclosure('最终外置行为与学习记忆', first(report.behavior_final, habitat.behavior, {})));
    initial.append(disclosure('初始蘑菇体局部学习设置与突触记忆', {controls:config.mb_learning_controls??null,learning_memory:config.learning_memory??null}));
    final.append(disclosure('最终蘑菇体局部学习设置、实际读出与突触记忆', {controls:report.mb_learning_controls_final??null,actual:report.mb_learning_actual??null,learning_memory:report.learning_memory??null}));
    const grid = node('div', undefined, 'summary-grid'); grid.append(initial, final); $('content').append(note('“初始条件重放”使用初始配置和种子，可按模型时间应用调整事件。“最终配置与学习记忆新实验”使用结束时的设置与外置记忆，但从初始姿态重新运行。旧记录缺少的数据不能补造。'), grid);
    const raw = section('原始配置'); raw.append(disclosure('查看 config.json 全文', config)); if (Object.keys(snapshot).length) raw.append(disclosure('查看最终监控快照', snapshot)); $('content').append(raw);
  }
  function showFiles() {
    const box = section('保存文件'); box.append(note('下载单个原始文件用于分析；“导出分享 ZIP”会打包该实验的数据与配置。程序依赖、连接组和模型资源仍需按安装文档另行准备，并遵守各自数据许可。'));
    const list = node('ul', undefined, 'file-list');
    for (const f of files()) { const li = node('li'); li.append(download(f.name), node('span', bytes(first(f.bytes, f.size)))); list.append(li); }
    if (!files().length) box.append(node('p', '没有可下载文件。', 'empty-inline')); box.append(list); $('content').append(box);
  }
  function showMedia() {
    const available = files().filter(f => /\.(png|jpe?g|webp|mp4|webm|m4v)$/i.test(f.name)); if (!available.length) return false;
    const box = section('保存的图像与录像'), grid = node('div', undefined, 'media-grid');
    for (const f of available) {
      const figure = node('figure', undefined, 'media'); const isVideo = /\.(mp4|webm|m4v)$/i.test(f.name); const media = node(isVideo ? 'video' : 'img'); media.src = filePath(f.name);
      if (isVideo) { media.controls = true; media.preload = 'metadata'; } else { media.alt = `实验保存图像：${f.name}`; media.loading = 'lazy'; }
      const caption = node('figcaption'); caption.append(download(f.name), node('span', ` · ${bytes(first(f.bytes, f.size))}`)); figure.append(media, caption); grid.append(figure);
    }
    box.append(grid); $('content').append(box); return true;
  }
  function table(rows) {
    if (!rows.length) return node('p', '没有可显示的记录。该轮可能未开启此项记录，或在产生数据前结束。', 'empty-inline');
    const keys = [...new Set(rows.slice(0, 50).flatMap(row => row && typeof row === 'object' && !Array.isArray(row) ? Object.keys(row) : ['记录']))].slice(0, 40);
    const scroll = node('div', undefined, 'table-scroll'), t = node('table', undefined, 'data-table'), head = node('thead'), tr = node('tr'), body = node('tbody');
    for (const key of keys) tr.append(node('th', key)); head.append(tr); t.append(head);
    for (const row of rows.slice(0, 500)) { const line = node('tr'); for (const key of keys) { const text = display(key === '记录' ? row : row?.[key]); const cell = node('td', text.length > 500 ? `${text.slice(0, 500)}…` : text); cell.title = text.slice(0, 2000); line.append(cell); } body.append(line); }
    t.append(body); scroll.append(t); return scroll;
  }
  function sampleNote(result, rows) {
    const lines = [`当前预览 ${rows.length} 条，单次最多请求 500 条。曲线连接的是预览点，不代表相邻保存时刻。`];
    if (result.sampled) lines.push('本次预览经过抽样，并未显示全部记录。');
    if (result.prefix_only) lines.push('本次只预览文件前段，后续时段没有纳入预览；不可据此判断完整实验。');
    if (result.response_byte_limited) lines.push('预览达到响应字节上限，显示的数据量受到限制。');
    if (present(result.oversized_rows_skipped) && Number(result.oversized_rows_skipped) > 0) lines.push(`因单行过大而跳过：${display(result.oversized_rows_skipped)} 条。完整内容请下载原始文件。`);
    if (present(result.invalid_sample_rows_skipped) && Number(result.invalid_sample_rows_skipped) > 0) lines.push(`抽样中无法解析而跳过：${display(result.invalid_sample_rows_skipped)} 条。`);
    for (const key of ['sampling', 'sampling_note', 'sampling_description', 'note', 'message']) if (present(result[key])) lines.push(`${key === 'sampling' ? '采样信息：' : ''}${display(result[key])}`);
    for (const key of ['total_rows', 'total_records', 'returned_rows', 'stride', 'sample_every', 'invalid_rows', 'skipped_rows']) if (present(result[key])) lines.push(`${({total_rows:'文件记录总数', total_records:'文件记录总数', returned_rows:'返回条数', stride:'抽样间隔', sample_every:'抽样间隔', invalid_rows:'无法解析条数', skipped_rows:'跳过条数'})[key]}：${display(result[key])}`);
    if (result.truncated || result.events_truncated || result.incomplete) lines.push('数据或预览存在截断/不完整标记，不能视为完整事件历史。');
    if (Array.isArray(result.warnings)) lines.push(...result.warnings.map(display));
    return note([...new Set(lines)].join('\n'), Boolean(result.truncated || result.events_truncated || result.incomplete || result.prefix_only || result.response_byte_limited || Number(result.oversized_rows_skipped) > 0 || Number(result.invalid_sample_rows_skipped) > 0));
  }
  function chart(rows, kind) {
    const points = rows.map((r, i) => {
      if (kind === 'trajectory') return [finite(first(r.x_mm, r.position_mm?.[0], r.x)), finite(first(r.y_mm, r.position_mm?.[1], r.y))];
      const stamp = present(r.brain_time_ms) ? Number(r.brain_time_ms) / 1000 : first(r.time_s, r.time, i);
      const rate = finite(r.chunk_spikes) !== null && finite(r.chunk_ms) > 0 ? Number(r.chunk_spikes) * 1000 / Number(r.chunk_ms) : null;
      return [finite(stamp), rate];
    }).filter(p => p.every(v => v !== null));
    if (!points.length) return null;
    const canvas = node('canvas', undefined, `chart${kind === 'trajectory' ? ' chart-map' : ''}`); canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', kind === 'trajectory' ? '保存轨迹的二维抽样折线，横纵轴单位毫米' : '神经块放电总数除以实际块时长，横轴模型秒，纵轴次每秒');
    const draw = () => {
      const rect = canvas.getBoundingClientRect(); if (!rect.width || !rect.height) return; const ratio = Math.min(window.devicePixelRatio || 1, 2); canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
      const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio); const w = rect.width, h = rect.height, pad = {l: 72, r: 30, t: 28, b: 43};
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity; for (const [x,y] of points) { minX = Math.min(minX,x); maxX=Math.max(maxX,x); minY=Math.min(minY,y); maxY=Math.max(maxY,y); }
      if (minX === maxX) { minX -= 1; maxX += 1; } if (minY === maxY) { minY -= 1; maxY += 1; }
      if (kind === 'trajectory') {
        const sx = (maxX-minX)/(w-pad.l-pad.r), sy = (maxY-minY)/(h-pad.t-pad.b), scale = Math.max(sx,sy)*1.15;
        const cx=(minX+maxX)/2,cy=(minY+maxY)/2; minX=cx-scale*(w-pad.l-pad.r)/2; maxX=cx+scale*(w-pad.l-pad.r)/2; minY=cy-scale*(h-pad.t-pad.b)/2; maxY=cy+scale*(h-pad.t-pad.b)/2;
      } else { minY=0; maxY=Math.max(maxY*1.1,1); }
      const px=x=>pad.l+(x-minX)/(maxX-minX)*(w-pad.l-pad.r), py=y=>h-pad.b-(y-minY)/(maxY-minY)*(h-pad.t-pad.b);
      ctx.font='11px "Segoe UI",sans-serif'; ctx.lineWidth=1;
      for(let i=0;i<=4;i++) { const x=minX+(maxX-minX)*i/4,y=minY+(maxY-minY)*i/4; ctx.strokeStyle='#29404c'; ctx.beginPath();ctx.moveTo(px(x),pad.t);ctx.lineTo(px(x),h-pad.b);ctx.moveTo(pad.l,py(y));ctx.lineTo(w-pad.r,py(y));ctx.stroke();ctx.fillStyle='#9db4bf';ctx.textAlign='center';ctx.fillText(x.toFixed(2),px(x),h-pad.b+20);ctx.textAlign='right';ctx.fillText(Math.abs(y)>1000?y.toExponential(1):y.toFixed(1),pad.l-8,py(y)+4); }
      ctx.strokeStyle='#83ddbe';ctx.lineWidth=1.7;ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(px(x),py(y)):ctx.moveTo(px(x),py(y)));ctx.stroke();
      if(kind==='trajectory') { for(const [index,color,label] of [[0,'#83ddbe','起点'],[points.length-1,'#efb76b','终点']]) { const [x,y]=points[index];ctx.fillStyle=color;ctx.beginPath();ctx.arc(px(x),py(y),4,0,Math.PI*2);ctx.fill();ctx.textAlign='left';ctx.fillText(label,px(x)+7,py(y)-7); } }
      ctx.fillStyle='#9db4bf';ctx.textAlign='left';ctx.fillText(kind==='trajectory'?'y / 毫米':'总放电率 / 次每秒',pad.l,16);ctx.textAlign='right';ctx.fillText(kind==='trajectory'?'x / 毫米':'模型时间 / 秒',w-pad.r,h-5);
    };
    state.chartObserver = new ResizeObserver(draw); state.chartObserver.observe(canvas); requestAnimationFrame(draw); return canvas;
  }
  async function renderTab() {
    if (!state.detail) return;
    const request = ++state.previewRequest, id = state.selected, tab = state.tab; clearChart(); $('content').replaceChildren(); $('content').setAttribute('aria-labelledby', `tab-${tab}`);
    if (tab === 'overview') { showOverview(); return; } if (tab === 'config') { showConfig(); return; } if (tab === 'files') { showFiles(); return; }
    if (tab === 'retina') showMedia();
    if (tab === 'mb_learning') {
      $('content').append(note('使用实际脑块的气味、KC/MBON放电、教学、资格迹与局部增益。新版记录的观察器保留有界的最近窗口；完整逐块数据请下载 mb_learning.jsonl。旧记录缺少这些字段时不补造。'));
      if (files().some(file=>file.name==='mb_observer.json')) {
        const link=node('a','打开已保存的学习观察器 ↗','button');
        link.href=`/assets/mb_observer.html?observer_run=${encodeURIComponent(id)}`;link.target='_blank';link.rel='noopener';
        $('content').append(link,download('mb_observer.json','下载完整观察器 JSON'));
      }
    }
    const loading = node('p', '正在读取数据预览…', 'empty-inline'); $('content').append(loading);
    try {
      const result = await api(apiPath(id, `/preview?kind=${encodeURIComponent(tab)}&limit=500`)); if (request !== state.previewRequest) return;
      loading.remove(); const rows = Array.isArray(result.rows) ? result.rows : []; $('content').append(sampleNote(result, rows));
      if (tab === 'neural') $('content').append(note('神经曲线使用每个保存块的实际放电总数 ÷ 该块模型时长；不是逐神经元栅格，也不是全部神经元的平均放电率。历史文件未保存完整 activity 事件，不能恢复逐神经元全程活动。'));
      if (tab === 'events') $('content').append(note('这里是参数、场景、奖励及状态等事件。旧记录不一定包含每类调整的完整内容；事件重放仅使用服务确认可重放的事件，不会把缺少的历史补齐。'));
      if (tab === 'behavior') $('content').append(note('学习值是外置的气味价值记忆，与全脑突触可塑性不同。保存的最终记忆可用于新一轮实验，不代表恢复原轮的全部内部状态。'));
      if (tab === 'trajectory' || tab === 'neural') { const figure = chart(rows, tab); if (figure) $('content').append(figure, node('p', tab === 'trajectory' ? '按真实位置绘制；为保持比例，坐标范围随已保存轨迹调整。只显示轨迹，不代表场景中的全部几何。' : '抽样仅用于显示；分析时请下载 neural.jsonl。', 'chart-caption')); }
      const box = section(`${tabs.find(t => t[0] === tab)[1]} · 原始字段预览`); box.append(table(rows)); $('content').append(box);
    } catch (error) { if (request === state.previewRequest) { loading.remove(); $('content').append(note(`未能读取预览：${error.message}。如已有文件，可到“全部文件”下载原始数据。`, true)); } }
  }
  async function importZip(prepareRun = false) {
    const file = $('importFile').files[0]; if (!file || state.busy) return; state.busy = true; $('importButton').disabled = $('importRunButton').disabled = true; $('importFile').disabled = true; renderReproduce(); message(`正在导入 ${file.name}（${bytes(file.size)}），请等待导入完成…`);
    try {
      const result = await api('/api/experiments/import', {method: 'POST', headers: {'Content-Type': 'application/zip'}, body: file});
      state.busy = false; await refreshList(first(result.id, result.run?.id));
      if (prepareRun) {
        await capabilities();
        message(`导入完成，已选中这份记录。请检查配置，再点击“初始条件重放”或“最终配置与学习记忆新实验”；独立查看器请复制启动命令。当前正在进行的实验不会自动结束。\n${$('reproduceReason').textContent}`, 'success');
        $('reproduceTitle').scrollIntoView({block:'center'});
        if (!$('reproduceInitial').disabled) $('reproduceInitial').focus({preventScroll:true}); else if (!$('cliBox').hidden) $('cliCommand').focus({preventScroll:true});
      } else message(`导入完成。${display(first(result.message, '数据已保存在本地，原记录不会因此被覆盖。'))}`, 'success');
      $('importFile').value = '';
    } catch (error) { message(`导入失败：${error.message}`, 'error'); }
    finally { state.busy = false; $('importFile').disabled = false; $('importButton').disabled = $('importRunButton').disabled = !$('importFile').files.length; renderReproduce(); }
  }
  $('refresh').addEventListener('click', () => { if (!state.busy) { capabilities(); refreshList(); } }); $('search').addEventListener('input', renderList);
  $('importFile').addEventListener('change', () => { $('importButton').disabled = $('importRunButton').disabled = !$('importFile').files.length || state.busy; }); $('importButton').addEventListener('click', () => importZip(false)); $('importRunButton').addEventListener('click', () => importZip(true));
  $('recordSaveAs').addEventListener('click', () => { if (!$('recordSaveAs').disabled && state.detail && !state.busy) window.ExperimentIO?.openSaveAs({runId:state.selected}); });
  $('reproduceInitial').addEventListener('click', () => reproduce('initial')); $('reproduceFinal').addEventListener('click', () => reproduce('final'));
  $('copyCommand').addEventListener('click', async () => { try { await navigator.clipboard.writeText($('cliCommand').value); message('启动命令已复制。'); } catch (_) { $('cliCommand').focus(); $('cliCommand').select(); message('请按 Ctrl+C 复制选中的启动命令。'); } });
  capabilities();
  const query = new URLSearchParams(window.location.search);
  refreshList(query.get('run')).then(() => {if (query.get('import') === '1') {message('选择实验 ZIP，然后点击“仅导入查看”或“导入后运行…”。导入后仍需确认运行方式，不会自动停止当前实验。');$('importFile').focus();}});
})();
