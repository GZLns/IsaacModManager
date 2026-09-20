/* =====================================================================
   以撒 Mod 管理器 · 前端
   状态驱动渲染 + 动画 + 视差; 所有数据来自 Python 后端的 JSON API
   ===================================================================== */
'use strict';

const $ = (id) => document.getElementById(id);
const state = {
  mods: [], groups: [], stats: {}, cfg: {}, logs: [],
  selected: new Set(),
  filter: 'all', sort: 'name', search: '',
  showDisabled: true, loading: true,
};

/* ---------------- 小工具 ---------------- */
async function api(path, data) {
  const opt = { method: data === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' } };
  if (data !== undefined) opt.body = JSON.stringify(data);
  const res = await fetch(path, opt);
  const json = await res.json().catch(() => ({ ok: false, error: '返回数据解析失败' }));
  if (!json.ok) throw new Error(json.error || '操作失败');
  return json;
}

function apply(s) {
  state.mods = s.mods || [];
  state.groups = s.groups || [];
  state.stats = s.stats || {};
  state.cfg = s.cfg || {};
  state.logs = s.logs || [];
  state.loading = false;
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = `<span></span><i class="bar"></i>`;
  el.firstChild.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 400); }, 3200);
}

function setStatus(msg) {
  const el = $('statusline');
  el.textContent = msg;
  el.classList.remove('flash');
  void el.offsetWidth;
  el.classList.add('flash');
}

/* ---------------- 视差 + 粒子 ---------------- */
function initParallax() {
  let mx = 0, my = 0, tx = 0, ty = 0;
  const root = document.documentElement;
  addEventListener('mousemove', (e) => {
    mx = (e.clientX / innerWidth) * 2 - 1;
    my = (e.clientY / innerHeight) * 2 - 1;
  }, { passive: true });

  const gridWrap = document.querySelector('.grid-wrap');
  gridWrap.addEventListener('scroll', () => {
    root.style.setProperty('--sy', gridWrap.scrollTop.toFixed(1));
  }, { passive: true });

  (function loop() {
    tx += (mx - tx) * 0.08;
    ty += (my - ty) * 0.08;
    root.style.setProperty('--mx', tx.toFixed(4));
    root.style.setProperty('--my', ty.toFixed(4));
    requestAnimationFrame(loop);
  })();
}

function initDust() {
  const cv = $('dust'), ctx = cv.getContext('2d');
  let w, h, dots = [];
  const resize = () => {
    w = cv.width = cv.offsetWidth; h = cv.height = cv.offsetHeight;
    dots = Array.from({ length: Math.round(w * h / 26000) }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      r: Math.random() * 1.8 + 0.5, a: Math.random() * 0.5 + 0.15,
      vx: (Math.random() - 0.5) * 0.22, vy: -Math.random() * 0.3 - 0.05,
    }));
  };
  resize();
  addEventListener('resize', resize);
  (function draw() {
    ctx.clearRect(0, 0, w, h);
    for (const d of dots) {
      d.x += d.vx; d.y += d.vy;
      if (d.y < -6) { d.y = h + 6; d.x = Math.random() * w; }
      if (d.x < -6) d.x = w + 6; if (d.x > w + 6) d.x = -6;
      ctx.beginPath();
      ctx.fillStyle = `rgba(200,215,255,${d.a})`;
      ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
      ctx.fill();
    }
    requestAnimationFrame(draw);
  })();
}

/* ---------------- 卡片 3D 倾斜 ---------------- */
function bindTilt(card) {
  card.addEventListener('mousemove', (e) => {
    const r = card.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    card.style.transform =
      `perspective(1200px) rotateY(${px * 7}deg) rotateX(${-py * 7}deg) translateY(-6px) scale(1.02)`;
  });
  card.addEventListener('mouseleave', () => { card.style.transform = ''; });
}

/* ---------------- 渲染: 侧边栏 ---------------- */
function renderNav() {
  const nav = $('nav');
  const cur = state.view || 'all';
  const items = [];
  const enabledTotal = state.stats.enabled || 0;

  // 入门指引放在最前: 新装这个工具的人第一眼就该看到
  items.push(`<div class="nav-item nav-guide" data-act="guide">
      <span class="ico">◆</span><span class="txt">Mod 入门</span>
      <span class="badge">新</span></div>`);

  items.push(`<div class="nav-item ${cur === 'all' ? 'active' : ''}" data-view="all">
      <span class="ico">▦</span><span class="txt">全部 Mod</span>
      <span class="badge">${state.mods.length}</span></div>`);

  items.push(`<div class="nav-sep">分组 · 右键操作</div>`);
  state.groups.forEach((g, i) => {
    items.push(`<div class="nav-item ${cur === g.name ? 'active' : ''}" data-view="${esc(g.name)}"
        data-group="${esc(g.name)}" data-priority="${i}">
        <span class="ico">◈</span><span class="txt" title="优先级 P${i}（右键分组可上移/下移）">${esc(g.name)}</span>
        <span class="badge">${g.enabled}/${g.total}</span></div>`);
  });
  if (state.mods.some(m => !m.group)) {
    items.push(`<div class="nav-item ${cur === '__ungrouped__' ? 'active' : ''}" data-view="__ungrouped__">
        <span class="ico">▢</span><span class="txt">未分组</span>
        <span class="badge">${state.mods.filter(m => !m.group).length}</span></div>`);
  }
  items.push(`<div class="nav-item nav-add" data-act="new-group">
      <span class="ico">＋</span><span class="txt">新建分组</span></div>`);

  nav.innerHTML = items.join('');
  nav.querySelectorAll('.nav-item').forEach((el) => {
    el.addEventListener('click', () => {
      if (el.dataset.act === 'new-group') return promptNewGroup();
      if (el.dataset.act === 'guide') return openGuide();
      state.view = el.dataset.view || 'all';
      renderNav(); renderGrid();
    });
    if (el.dataset.group) {
      el.addEventListener('contextmenu', (e) => { e.preventDefault(); groupMenu(e, el.dataset.group); });
      el.addEventListener('dblclick', () => confirmSwitch(el.dataset.group));
    }
  });
  void enabledTotal;
}

/* ---------------- 渲染: 卡片 ---------------- */
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function colorOf(name) {
  const palette = [
    ['#3b4a76', '#1e2743'], ['#6b3b52', '#331a2a'], ['#2f6350', '#14312a'],
    ['#4a3d73', '#241c3d'], ['#6b5530', '#332614'], ['#2f5568', '#14262f'],
    ['#5a2f6b', '#2a1433'], ['#3d6b2f', '#1c3114'],
  ];
  let h = 0; for (const ch of name) h += ch.charCodeAt(0);
  const c = palette[h % palette.length];
  return `linear-gradient(150deg, ${c[0]}, ${c[1]})`;
}

function visibleMods() {
  const kw = state.search.trim().toLowerCase();
  let list = state.mods.filter((m) => {
    if (kw && !m.name.toLowerCase().includes(kw) && !m.dir.toLowerCase().includes(kw)) return false;
    if (state.filter !== 'all' && m.state !== state.filter) return false;
    if (!state.showDisabled && m.state !== 'enabled') return false;
    if (state.view === '__ungrouped__') return !m.group;
    if (state.view && state.view !== 'all' && state.view !== '__ungrouped__') return m.group === state.view;
    return true;
  });
  const rank = { enabled: 0, disabled: 1, unimported: 2 };
  const cmp = {
    name: (a, b) => a.name.localeCompare(b.name, 'zh'),
    version: (a, b) => String(a.version).localeCompare(String(b.version)),
    dir: (a, b) => a.dir.localeCompare(b.dir),
    state: (a, b) => rank[a.state] - rank[b.state],
  }[state.sort];
  return list.sort((a, b) => (rank[a.state] - rank[b.state]) || cmp(a, b));
}

function badgeFor(m) {
  if (m.dead_link) return '<span class="badge dead">失效链接</span>';
  if (m.state === 'enabled') return '<span class="badge on"><i class="dot"></i>已启用</span>';
  if (m.state === 'unimported') return '<span class="badge raw">未导入</span>';
  return '<span class="badge off">已停用</span>';
}

function renderGrid() {
  const grid = $('grid');
  if (state.loading) {
    grid.innerHTML = Array.from({ length: 8 }).map(() => '<div class="skeleton"></div>').join('');
    $('empty').hidden = true;
    return;
  }
  const list = visibleMods();
  $('empty').hidden = list.length > 0;
  grid.innerHTML = list.map((m, i) => {
    const sel = state.selected.has(m.dir) ? 'selected' : '';
    const grp = m.group ? `<span class="badge grp">${esc(m.group)}</span>` : '<span></span>';
    const isOn = m.state === 'enabled';
    const pill = isOn
      ? `<button class="pill on" data-toggle="${esc(m.dir)}" data-to="0"><span>✖</span>禁 用</button>`
      : `<button class="pill off" data-toggle="${esc(m.dir)}" data-to="1"><span>✔</span>启 用</button>`;
    return `<article class="card ${sel} ${m.dead_link ? 'dead' : ''}" data-dir="${esc(m.dir)}" style="--d:${Math.min(i * 38, 640)}ms">
      <div class="card-cover" style="--fallback:${colorOf(m.name)}">
        <span class="kind">TBOI MOD</span>
        <div class="badges">${grp}${badgeFor(m)}</div>
        <span class="check-ring"></span>
      </div>
      <div class="card-body">
        <div class="card-name">${esc(m.name)}</div>
        <div class="card-meta">${esc(m.version || 'v?')} · ${esc(m.dir)}</div>
        <div class="card-foot">${pill}
          <button class="more" data-menu="${esc(m.dir)}" title="更多操作">⋮</button>
        </div>
      </div>
    </article>`;
  }).join('');

  grid.querySelectorAll('.card').forEach((card) => {
    const dir = card.dataset.dir;
    bindTilt(card);
    card.addEventListener('click', (e) => {
      if (e.target.closest('[data-toggle]') || e.target.closest('[data-menu]')) return;
      if (e.ctrlKey || e.metaKey) {
        state.selected.has(dir) ? state.selected.delete(dir) : state.selected.add(dir);
      } else {
        state.selected.has(dir) ? state.selected.delete(dir) : state.selected.add(dir);
      }
      updateSelectionUI();
    });
    card.addEventListener('dblclick', (e) => {
      if (e.target.closest('[data-toggle]') || e.target.closest('[data-menu]')) return;
      toggleMod(dir, !(state.mods.find(m => m.dir === dir) || {}).state || state.mods.find(m => m.dir === dir).state !== 'enabled');
    });
    card.addEventListener('contextmenu', (e) => { e.preventDefault(); cardMenu(e, dir); });
  });
  grid.querySelectorAll('[data-toggle]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      ripple(e, btn);
      toggleMod(btn.dataset.toggle, btn.dataset.to === '1');
    });
  });
  grid.querySelectorAll('[data-menu]').forEach((btn) => {
    btn.addEventListener('click', (e) => { e.stopPropagation(); cardMenu(e, btn.dataset.menu); });
  });
  renderStats();
}

function updateSelectionUI() {
  document.querySelectorAll('.card').forEach((c) => {
    c.classList.toggle('selected', state.selected.has(c.dataset.dir));
  });
  renderStats();
}

/* ---------------- 渲染: 统计 ---------------- */
function renderStats() {
  const s = state.stats;
  const ringPct = s.total ? Math.round((s.enabled / s.total) * 100) : 0;
  const C = 2 * Math.PI * 17;
  $('stats').innerHTML =
    `<span>总文件数 <b class="stat-num" data-num="${s.total}">0</b></span>
     <span>已启用 <b class="stat-num" data-num="${s.enabled}">0</b></span>
     <span>已停用 <b class="stat-num" data-num="${s.disabled}">0</b></span>
     <span>未导入 <b class="stat-num" data-num="${s.unimported}">0</b></span>
     <span>已选择 <b class="stat-num" data-num="${state.selected.size}">0</b></span>
     <span class="ring" title="启用比例 ${ringPct}%">
       <svg width="42" height="42" viewBox="0 0 42 42">
         <defs><linearGradient id="gradRing" x1="0" y1="0" x2="1" y2="1">
           <stop offset="0%" stop-color="#6c5ce7"/><stop offset="100%" stop-color="#3d7bfd"/>
         </linearGradient></defs>
         <circle class="bgc" cx="21" cy="21" r="17"/>
         <circle class="fgc" cx="21" cy="21" r="17" stroke-dasharray="${C.toFixed(1)}"
                 stroke-dashoffset="${(C - C * ringPct / 100).toFixed(1)}"/>
       </svg></span>`;
  document.querySelectorAll('.stat-num').forEach((el) => {
    const target = Number(el.dataset.num) || 0;
    const from = Number(el.textContent) || 0;
    if (from === target) { el.textContent = target; return; }
    el.classList.add('pop');
    const t0 = performance.now(), dur = 520;
    (function step(now) {
      const k = Math.min((now - t0) / dur, 1);
      el.textContent = Math.round(from + (target - from) * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step); else el.classList.remove('pop');
    })(t0);
  });
  if (state.logs.length) setStatus(state.logs[state.logs.length - 1]);
}

function renderPath() {
  const c = state.cfg;
  $('pathText').textContent = `mods: ${c.mods_path_short || c.mods_path}    仓库: ${c.library_path_short || c.library_path}    (点击改路径)`;
  document.documentElement.style.setProperty('--bg-opacity', c.bg_enabled ? (c.bg_opacity ?? .5) : 0);
  document.documentElement.style.setProperty('--card-alpha', c.card_opacity ?? .4);
  document.querySelector('.bg').style.display = c.has_bg ? '' : 'none';
}

function render() { renderNav(); renderGrid(); renderPath(); }

/* ---------------- 交互 ---------------- */
function ripple(e, el) {
  const r = el.getBoundingClientRect();
  const d = Math.max(r.width, r.height);
  const span = document.createElement('span');
  span.className = 'ripple';
  span.style.width = span.style.height = d + 'px';
  span.style.left = (e.clientX - r.left - d / 2) + 'px';
  span.style.top = (e.clientY - r.top - d / 2) + 'px';
  el.appendChild(span);
  setTimeout(() => span.remove(), 620);
}

async function call(path, data, okMsg) {
  try {
    // 动作接口一律用 POST; 不传参数时也发空对象, 否则会被当成 GET -> 后端 404 not found
    const s = await api(path, data || {});
    apply(s);
    state.selected = new Set([...state.selected].filter(d => state.mods.some(m => m.dir === d)));
    render();
    if (okMsg) toast(okMsg);
    return s;                      // ★ 必须返回: 调用方要用返回的数据 (如工坊查询结果)
  } catch (err) {
    toast(err.message || '操作失败', 'err');
    await refresh();
    return null;
  }
}

async function refresh() {
  try { apply(await api('/api/state')); render(); }
  catch (e) { toast('无法连接后端: ' + e.message, 'err'); }
}

function toggleMod(dirs, enable) {
  const list = Array.isArray(dirs) ? dirs : [dirs];
  if (!list.length) return;
  if (list.length > 1) call('/api/mods/batch', { dirs: list, enable });   // 多选走批量接口
  else call('/api/mods/toggle', { dir: list[0], enable });
}


/* ==================== Steam 创意工坊: 剪贴板链接 → 添加 mod ==================== */
const WS_RE = /(?:[?&]id=(\d{6,12}))|(?:CommunityFilePage\/(\d{6,12}))|(?:^|\s)(\d{6,12})(?:\s|$)/;

function extractWsId(text) {
  const m = String(text || '').match(WS_RE);
  return m ? (m[1] || m[2] || m[3] || '') : '';
}

function fmtSize(n) {
  n = Number(n) || 0;
  if (!n) return '大小未知';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(2) + ' MB';
}

function fmtTime(ts) {
  ts = Number(ts) || 0;
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
    + '-' + String(d.getDate()).padStart(2, '0');
}

async function readClipboardText() {
  try {
    if (navigator.clipboard && navigator.clipboard.readText) {
      return await navigator.clipboard.readText();
    }
  } catch (e) { /* 浏览器可能不给权限, 走手输 */ }
  return '';
}

/* 入口: 读剪贴板 → 有链接就直接查, 否则给手输框 */
async function workshopFromClipboard() {
  const txt = await readClipboardText();
  const pid = extractWsId(txt);
  if (pid) return workshopLookup(pid);
  workshopManual(txt);
}

function workshopManual(prefill) {
  modal(`<h3>从创意工坊添加</h3>
    <div class="row"><input type="text" id="wsInput" placeholder="粘贴创意工坊链接或直接填数字 ID"
      value="${esc((prefill || '').slice(0, 200))}"></div>
    <div class="hint" style="color:var(--fg-dim);font-size:13px;line-height:1.8;margin-top:10px">
      支持：<span style="color:var(--fg-soft)">steamcommunity.com/sharedfiles/filedetails/?id=数字</span><br>
      也可以直接填纯数字 ID。浏览器有时不给自动读剪贴板的权限，那就粘贴到这里。
    </div>
    <div class="modal-actions">
      <button class="btn ghost" id="wsCancel">取消</button>
      <button class="btn green" id="wsOk">查询</button></div>`, (box) => {
    const inp = box.querySelector('#wsInput');
    setTimeout(() => inp.focus(), 60);
    const go = () => {
      const pid = extractWsId(inp.value) || (/^\d{6,12}$/.test(inp.value.trim()) ? inp.value.trim() : '');
      if (!pid) return toast('没识别出创意工坊 ID', 'err');
      closeModal();
      workshopLookup(pid);
    };
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    box.querySelector('#wsOk').onclick = go;
    box.querySelector('#wsCancel').onclick = closeModal;
  });
}

async function workshopLookup(pid) {
  let r;
  try {
    r = await call('/api/workshop/lookup', { text: pid });
  } catch (e) { return; }
  if (!r || !r.ok) return;         // call 失败时已经 toast 过了
  showWorkshopDialog(pid, r.ws_info || {}, r.ws_status || {});
}

async function showWorkshopDialog(pid, info, st) {
  const title = info.title || ('创意工坊 ' + pid);
  let login = { logged_in: false, user: null };
  try {
    const ls = await api('/api/workshop/status', {});
    login = { logged_in: !!ls.logged_in, user: ls.steam_user || null };
  } catch (e) { /* 当作未登录 */ }

  let stateText, stateColor;
  if (st.in_library) { stateText = '已在你的 mod 仓库里'; stateColor = '#5fd97e'; }
  else if (st.downloaded) { stateText = 'Steam 里已下载（可直接导入，也可重新下载）'; stateColor = '#ffd479'; }
  else { stateText = '还没下载过'; stateColor = '#a3adc2'; }

  modal(`<h3>创意工坊 mod</h3>
    <div class="row" style="line-height:2">
      <div style="font-size:17px;font-weight:700">${esc(title)}</div>
      <div style="color:var(--fg-soft);font-size:13px">ID ${esc(pid)} · ${fmtSize(info.size)} · 更新于 ${fmtTime(info.updated)}</div>
      <div style="font-size:13px;margin-top:6px">本地状态：<b style="color:${stateColor}">${stateText}</b></div>
    </div>
    <div class="hint" style="color:var(--fg-dim);font-size:13px;line-height:1.9;margin-top:12px">
      <b style="color:var(--fg-soft)">下载方式</b>：以撒的工坊内容是「文件夹」形式，Steam 不对它开放直链，
      只能由 Steam 客户端下载（实测它的 file_url 是空的，这点和 L4D2 那种单文件 mod 不同）。
      <br>点「↓ 下载到我的仓库」会唤起 Steam 工坊页，<b style="color:#5fd97e">在 Steam 里点一下「订阅」</b>，
      下完这里会自动导入并启用；内容存进你自己的 mod 仓库，之后可以在 Steam 里取消订阅。
      ${login.logged_in ? `<br>当前登录：<b style="color:#5fd97e">${esc(login.user || '已登录')}</b>` : ''}
    </div>
    <div class="modal-actions">
      <button class="btn ghost" id="wsClose">关闭</button>
      <button class="btn" id="wsSteam">在 Steam 中打开</button>
      ${!login.logged_in ? `<button class="btn" id="wsLogin">登录 Steam</button>` : ''}
      ${st.downloaded && !st.in_library ? `<button class="btn" id="wsImport">导入已下载的</button>` : ''}
      ${!st.in_library ? `<button class="btn green" id="wsDl">↓ 下载到我的仓库</button>` : ''}
    </div>`, (box) => {
    box.querySelector('#wsClose').onclick = () => closeModal();
    box.querySelector('#wsSteam').onclick = () => openInSteam(pid);
    const lg = box.querySelector('#wsLogin');
    if (lg) lg.onclick = () => startWsLogin();
    const imp = box.querySelector('#wsImport');
    if (imp) imp.onclick = () => { closeModal(); call('/api/workshop/add', { id: pid }, '已导入并启用'); };
    const dl = box.querySelector('#wsDl');
    if (dl) dl.onclick = () => startWsDownload(pid, title);
  });
}

/* ---------- 应用内搜索创意工坊（读工坊浏览页内嵌的官方 JSON） ---------- */
let wsQ = { text: '', sort: 'trend', page: 1, pages: 1, total: 0, busy: false, loaded: false };

/* ---------- Mod 入门(内容在 index.html 里, 这里只管开关与切页) ---------- */
function openGuide(sec) {
  const p = $('gdPanel');
  if (!p) return;
  p.hidden = false;
  if (sec) gdShow(sec);
}
function closeGuide() { const p = $('gdPanel'); if (p) p.hidden = true; }

function gdShow(n) {
  const body = $('gdPanel');
  if (!body) return;
  body.querySelectorAll('.gd-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.gd === String(n));
  });
  body.querySelectorAll('.gd-sec').forEach(s => {
    s.hidden = (s.dataset.gd !== String(n));
  });
  const b = body.querySelector('.gd-body');
  if (b) b.scrollTop = 0;
}

function bindGuide() {
  const p = $('gdPanel');
  if (!p) return;
  const tabs = p.querySelectorAll('.gd-tab');
  tabs.forEach(t => { t.onclick = () => gdShow(t.dataset.gd); });
  const close = p.querySelector('#gdClose');
  if (close) close.onclick = closeGuide;
  p.querySelectorAll('[data-gd-close]').forEach(el => { el.onclick = closeGuide; });
}

function openWsSearch() {
  $('wsPanel').hidden = false;
  if (!wsQ.loaded) { wsSearch(); }
  setTimeout(() => $('wsQuery').focus(), 60);
}

function closeWsSearch() { $('wsPanel').hidden = true; }

async function wsSearch(page) {
  if (wsQ.busy) return;
  wsQ.busy = true;
  wsQ.text = $('wsQuery').value.trim();
  if (page) wsQ.page = page;
  $('wsResults').innerHTML = '<div class="wsp-empty">正在查询 Steam 创意工坊…</div>';
  try {
    const r = await api('/api/workshop/search',
      { text: wsQ.text, sort: wsQ.sort, page: wsQ.page });
    const d = (r && r.ws_search) || {};
    wsQ.loaded = true;
    wsQ.items = d.items || [];
    wsQ.page = d.page || 1;
    wsQ.pages = d.pages || 1;
    wsQ.total = d.total || 0;
    $('wsCount').textContent = `共 ${wsQ.total} 个结果`;
    $('wsNote').textContent = d.logged_in
      ? '（已登录：可直接下载）'
      : '（未登录也能下载，不用订阅）';
    $('wsPage').textContent = `第 ${wsQ.page} / ${wsQ.pages} 页`;
    renderWsResults();
  } catch (e) {
    $('wsResults').innerHTML =
      `<div class="wsp-empty">查询失败：${esc(e.message || '未知错误')}</div>`;
  } finally {
    wsQ.busy = false;
  }
}

function fmtNum(n) {
  n = Number(n) || 0;
  if (n >= 100000) return (n / 10000).toFixed(1) + '万';
  if (n >= 10000) return (n / 10000).toFixed(2) + '万';
  return String(n);
}

function renderWsResults() {
  const box = $('wsResults');
  const list = wsQ.items || [];
  if (!list.length) { box.innerHTML = '<div class="wsp-empty">没有找到结果，换个关键词试试</div>'; return; }
  // 注意: 这里的关键尺寸都用内联样式写死 —— 外链样式里的高度在这类嵌套里
  // 容易被别的规则盖掉, 内联最稳(踩过一次: 缩略图塌成 50px 且正文不显示)
  box.innerHTML = list.map(it => {
    const badge = it.in_library
      ? '<span class="wsp-badge on">已在仓库</span>'
      : (it.downloaded ? '<span class="wsp-badge got">Steam 已下载</span>' : '');
    const ph = `<div class="ph" style="position:absolute;z-index:0;inset:0;display:grid;
      place-items:center;font-size:34px;font-weight:800;color:rgba(255,255,255,.5);
      background:linear-gradient(150deg,#4a3d73,#241c3d)">${esc((it.title || '?').slice(0, 1).toUpperCase())}</div>`;
    const img = it.preview
      ? `<img src="/api/workshop/thumb?u=${encodeURIComponent(it.preview)}" alt=""
             loading="lazy" decoding="async"
             style="position:relative;z-index:1;display:block;width:100%;height:150px;object-fit:cover"
             onerror="this.style.display='none'">`
      : '';
    return `<div class="wsp-card" data-id="${esc(it.id)}" style="display:block">
      <div class="wsp-thumb" style="position:relative;height:150px;overflow:hidden;background:rgba(0,0,0,.4)">
        ${ph}${img}${badge}</div>
      <div class="wsp-body" style="display:block;padding:10px 12px 12px">
        <div class="wsp-name" style="display:block;font-size:14.5px;font-weight:700;color:#fff;
          line-height:1.4;overflow:hidden">${esc(it.title || '(无标题)')}</div>
        ${it.desc ? `<div class="wsp-desc" style="display:block;font-size:12px;color:#a3adc2;
          line-height:1.5;margin-top:6px;overflow:hidden">${esc(it.desc)}</div>` : ''}
        <div class="wsp-stats" style="display:flex;flex-wrap:wrap;gap:12px;margin-top:9px;
          font-size:12px;color:#a3adc2">
          <span>👍 <b style="color:#ffd479">${fmtNum(it.votes_up)}</b></span>
          ${it.stars ? `<span>★ <b style="color:#ffd479">${it.stars}</b></span>` : ''}
          ${it.subs ? `<span>订阅 <b style="color:#ffd479">${fmtNum(it.subs)}</b></span>` : ''}
          ${it.size ? `<span>${fmtSize(it.size)}</span>` : ''}
        </div>
      </div>
    </div>`;
  }).join('');
  box.querySelectorAll('.wsp-card').forEach(el => {
    el.onclick = () => workshopLookup(el.dataset.id);
  });
}

function bindWsPanel() {
  $('btnSearchWs').onclick = () => openWsSearch();
  $('wsClose').onclick = () => closeWsSearch();
  document.querySelectorAll('[data-wsp-close]').forEach(el => el.onclick = () => closeWsSearch());
  $('wsGo').onclick = () => { wsQ.page = 1; wsSearch(1); };
  $('wsReset').onclick = () => {
    $('wsQuery').value = ''; wsQ.sort = 'trend'; wsQ.page = 1;
    document.querySelectorAll('.wsp-sort').forEach(b =>
      b.classList.toggle('is-active', b.dataset.sort === 'trend'));
    wsSearch(1);
  };
  $('wsQuery').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); wsQ.page = 1; wsSearch(1); }
  });
  $('wsPrev').onclick = () => { if (wsQ.page > 1) wsSearch(wsQ.page - 1); };
  $('wsNext').onclick = () => { if (wsQ.page < wsQ.pages) wsSearch(wsQ.page + 1); };
  document.querySelectorAll('.wsp-sort').forEach(b => b.onclick = () => {
    wsQ.sort = b.dataset.sort;
    document.querySelectorAll('.wsp-sort').forEach(x => x.classList.remove('is-active'));
    b.classList.add('is-active');
    wsSearch(1);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('wsPanel').hidden) closeWsSearch();
  });
}

/* ---------- 界面内登录 Steam(全程没有任何控制台窗口) ---------- */
function startWsLogin() {
  let step = 'cred';     // cred 输账号密码 / code 输验证码 / confirm 手机批准
  let mode = '';         // 'email' | 'device' | 'confirm'

  modal(`<h3>登录 Steam</h3>
    <div class="row" style="display:block;line-height:1.9;color:var(--fg-soft);font-size:14px">
      直连下载需要用你的账号向 Steam 申请内容密钥，<b>登录一次</b>就够。<br>
      密码会先用 Steam 给的公钥在本地加密再提交，<b style="color:#5fd97e">不会写进任何文件或日志</b>；
      登录成功后只留一张票据，之后下载都不再需要密码。
    </div>
    <div class="lg-form">
      <div id="lgCred">
        <label for="lgUser">Steam 账号</label>
        <input type="text" id="lgUser" autocomplete="username" spellcheck="false">
        <label for="lgPw">密码</label>
        <input type="password" id="lgPw" autocomplete="current-password">
      </div>
      <div id="lgCodeBox" hidden>
        <label for="lgCode" id="lgCodeLabel">验证码</label>
        <input type="text" id="lgCode" inputmode="numeric" autocomplete="one-time-code">
        <div class="lg-hint" id="lgHint"></div>
      </div>
      <div class="lg-msg" id="lgMsg" hidden></div>
    </div>
    <div class="modal-actions">
      <button class="btn ghost" id="lgCancel">取消</button>
      <button class="btn green" id="lgGo">登录</button>
    </div>`, (box) => {
    const $u = box.querySelector('#lgUser');
    const $p = box.querySelector('#lgPw');
    const $c = box.querySelector('#lgCode');
    const $cbox = box.querySelector('#lgCodeBox');
    const $lbl = box.querySelector('#lgCodeLabel');
    const $hint = box.querySelector('#lgHint');
    const $msg = box.querySelector('#lgMsg');
    const $go = box.querySelector('#lgGo');
    box.querySelector('#lgCancel').onclick = () => closeModal();
    $u.focus();

    // ★ 关键: 错误一律显示在弹窗里, 不飘到页面右下角
    const say = (text, kind) => {
      $msg.textContent = text || '';
      $msg.hidden = !text;
      $msg.className = 'lg-msg ' + (kind || 'err');
    };
    const busy = (b) => {
      $go.disabled = b;
      $go.textContent = b ? '处理中…' : (step === 'confirm' ? '我已批准，继续' : '登录');
    };

    const submit = async () => {
      say('');
      const user = $u.value.trim();
      const password = $p.value;
      const code = $c.value.trim();
      if (step === 'cred' && (!user || !password)) { say('账号和密码都要填'); return; }
      if (step === 'code' && !code) { say('请填写验证码'); return; }
      busy(true);
      try {
        const payload = { user: user, password: password, code: code, mode: mode };
        if (step === 'confirm') payload.action = 'poll';
        const r = await api('/api/workshop/login', payload);
        const L = (r && r.login) || {};
        if (L.ok) {
          closeModal();
          toast(`登录成功：${L.user}（以后下载不用再输密码）`);
          refresh();
          return;
        }
        if (L.need_code) {
          step = 'code';
          mode = L.need_code;
          $cbox.hidden = false;
          $lbl.textContent = (mode === 'email') ? '邮箱验证码' : '手机令牌验证码';
          $hint.textContent = (mode === 'email')
            ? 'Steam 已把验证码发到你的邮箱，请填写收到的验证码'
            : '请填写手机 Steam 应用里显示的验证码';
          $c.value = '';
          $c.focus();
          say(L.result_name || '', 'err');
          busy(false);
          return;
        }
        if (L.need_confirm) {
          step = 'confirm';
          mode = 'confirm';
          $cbox.hidden = true;
          say('请在手机 Steam 应用里点「批准」登录，然后点下面的按钮继续', 'info');
          busy(false);
          return;
        }
        say('登录失败：Steam 没有给出预期结果，请重试');
        busy(false);
      } catch (e) {
        say(e.message || '登录失败');          // 弹窗内显示, 不再用右下角提示
        busy(false);
      }
    };

    $go.onclick = submit;
    [$u, $p, $c].forEach(el => el.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); submit(); }
    }));
  });
}

/* ---------- 工坊下载: 唤起 Steam 客户端 + 自动守候 + 自动导入 ---------- */
let wsJobPoll = null;

function stopWsJobPoll() {
  if (wsJobPoll) { clearInterval(wsJobPoll); wsJobPoll = null; }
}

async function startWsDownload(pid, title) {
  closeModal();
  let r;
  try {
    r = await api('/api/workshop/download', { id: pid });
  } catch (e) {
    toast(e.message || '下载失败', 'err');
    return;
  }
  if (!r || !r.ok) return;
  toast(`已在 Steam 里打开「${title}」，请点「订阅」`);
  setStatus(`等待 Steam 下载「${title}」…（在 Steam 窗口点一下「订阅」）`);
  watchWsJob(title);
}

/* 轮询下载进度: 下载可能要几分钟, 实时输出放到状态栏, 不刷屏 */
function watchWsJob(title) {
  stopWsJobPoll();
  let n = 0;
  wsJobPoll = setInterval(async () => {
    n++;
    if (n > 1200) { stopWsJobPoll(); setStatus(''); toast('下载等待超时', 'err'); return; }
    let r;
    try {
      r = await api('/api/workshop/job', {});
    } catch (e) { return; }
    const j = r && r.ok ? r.ws_job : null;
    if (!j) return;
    if (j.running) {
      if (j.msg) setStatus(j.msg);
      return;
    }
    stopWsJobPoll();
    setStatus('');
    if (j.state === 'done') {
      toast(j.msg || `「${title}」下载完成`);
    } else {
      toast(j.msg || '下载失败', 'err');
    }
    refresh();
  }, 1500);
}

/* 列出 Steam 里已下载的工坊 mod, 一键导入 */
async function workshopSubscribed() {
  let r;
  try {
    r = await call('/api/workshop/subscribed', {});
  } catch (e) { return; }
  const list = r.ws_subscribed || [];
  const body = list.length
    ? list.map(x => `<div class="row" style="display:flex;align-items:center;gap:10px;line-height:1.6">
        <span style="flex:1">${esc(x.title)}<span style="color:var(--fg-dim);font-size:12px"> · ${fmtSize(x.size)}</span></span>
        ${x.imported
          ? '<span style="color:#5fd97e;font-size:13px">已导入</span>'
          : `<button class="btn green" data-import="${esc(x.id)}" style="padding:6px 12px">导入</button>`}
      </div>`).join('')
    : `<div style="color:var(--fg-dim);font-size:13px;line-height:1.8">
          Steam 工坊目录里没有已下载的 mod。<br>
          <span style="color:var(--fg-soft)">（${esc((state.cfg && state.cfg.workshop_dir) || '工坊目录未知')}）</span>
       </div>`;
  modal(`<h3>Steam 已下载的工坊 mod</h3>
    <div class="hint" style="color:var(--fg-dim);font-size:13px;margin-bottom:10px">
      这里列出 Steam 已经下载到本地的工坊内容，导入后会复制进你的 mod 仓库并启用。
    </div>
    <div style="max-height:44vh;overflow:auto">${body}</div>
    <div class="modal-actions"><button class="btn ghost" id="subClose">关闭</button></div>`, (box) => {
    box.querySelector('#subClose').onclick = closeModal;
    box.querySelectorAll('[data-import]').forEach(el => el.onclick = () => {
      closeModal();
      call('/api/workshop/add', { id: el.dataset.import }, '已导入并启用');
    });
  });
}

/* ---------------- 右键菜单 ---------------- */
function showMenu(x, y, html) {
  const m = $('ctxmenu');
  m.innerHTML = html;
  m.hidden = false;
  m.style.left = Math.min(x, innerWidth - 280) + 'px';
  m.style.top = Math.min(y, innerHeight - m.offsetHeight - 20) + 'px';
  const close = (ev) => {
    if (ev && m.contains(ev.target)) return;
    m.hidden = true;
    document.removeEventListener('mousedown', close);
    document.removeEventListener('contextmenu', close);
  };
  setTimeout(() => {
    document.addEventListener('mousedown', close);
    document.addEventListener('contextmenu', close);
  }, 0);
}

function cardMenu(e, dir) {
  const m = state.mods.find(x => x.dir === dir);
  // 右键一张「没被选中」的卡片 → 先把选择切到它 (与文件管理器一致),
  // 避免出现「菜单写着批量、实际只操作一个」的错位
  if (!state.selected.has(dir)) {
    state.selected.clear();
    state.selected.add(dir);
    updateSelectionUI();
  }
  const dirs = [...state.selected];            // ★ 批量: 对所有已选 mod 生效
  const multi = dirs.length > 1;
  const recs = dirs.map(x => state.mods.find(y => y.dir === x)).filter(Boolean);
  const allOn = recs.length > 0 && recs.every(r => r.state === 'enabled');
  const deads = recs.filter(r => r.dead_link);

  const grp = state.groups.map(g =>
    `<div class="item" data-assign="${esc(g.name)}">▸ 加入「${esc(g.name)}」</div>`).join('');
  const toggleRow = deads.length
    ? `<div class="item danger" data-clean="1">▸ 清理失效链接${multi ? '（' + deads.length + ' 条）' : ''}</div>`
    : `<div class="item" data-toggle="${allOn ? '0' : '1'}">${allOn ? '✖ 禁用' : '✔ 启用'}${multi ? ' 选中的 ' + dirs.length + ' 个' : ''}</div>`;

  showMenu(e.clientX, e.clientY, `
    <div class="title">${multi ? '已选 ' + dirs.length + ' 个 mod' : esc(m.name)}</div>
    ${toggleRow}
    <div class="sep"></div>${grp}
    <div class="item" data-newgroup="1">＋ 新建分组并加入${multi ? '（全部 ' + dirs.length + ' 个）' : ''}</div>
    ${recs.some(r => r.group) ? `<div class="item" data-ungroup="1">▸ 移出分组</div>` : ''}
    <div class="sep"></div>
    <div class="item" data-open="${esc(dir)}">▸ 打开 mod 文件夹</div>
    ${m.id ? `<div class="item" data-ws="${esc(m.id)}">▸ 在 Steam 中打开创意工坊</div>
              <div class="item" data-wsw="${esc(m.id)}">▸ 在浏览器打开创意工坊</div>` : ''}`);

  const menu = $('ctxmenu');
  menu.querySelectorAll('[data-toggle]').forEach(el => el.onclick = () => {
    menu.hidden = true; toggleMod(dirs, el.dataset.toggle === '1');
  });
  menu.querySelectorAll('[data-assign]').forEach(el => el.onclick = () => {
    menu.hidden = true; call('/api/groups/assign', { name: el.dataset.assign, dirs });
  });
  menu.querySelectorAll('[data-ungroup]').forEach(el => el.onclick = () => {
    menu.hidden = true; call('/api/groups/remove', { dirs });
  });
  menu.querySelectorAll('[data-newgroup]').forEach(el => el.onclick = async () => {
    menu.hidden = true;
    const name = await promptText('新建分组', '分组名称（如：忏悔 / 忏悔+）');
    if (!name) return;
    await call('/api/groups/create', { name });
    call('/api/groups/assign', { name, dirs });
  });
  menu.querySelectorAll('[data-open]').forEach(el => el.onclick = () => {
    menu.hidden = true;
    call('/api/open_dir', { path: state.cfg.library_path + '\\' + el.dataset.open });
  });
  menu.querySelectorAll('[data-ws]').forEach(el => el.onclick = () => {
    menu.hidden = true; openInSteam(el.dataset.ws);
  });
  menu.querySelectorAll('[data-wsw]').forEach(el => el.onclick = () => {
    menu.hidden = true;
    window.open('https://steamcommunity.com/sharedfiles/filedetails/?id=' + el.dataset.wsw, '_blank');
  });
  menu.querySelectorAll('[data-clean]').forEach(el => el.onclick = async () => {
    menu.hidden = true;
    const todo = deads.length ? deads.map(r => r.dir) : [el.dataset.clean];
    for (const x of todo) await call('/api/mods/clean_dead', { dir: x });
  });
}

/* 用 steam:// 协议直接唤起 Steam 客户端打开创意工坊页 (装了 Steam 就秒开, 不会跳浏览器) */
function openInSteam(id) {
  const f = document.createElement('iframe');
  f.style.cssText = 'display:none';
  f.src = 'steam://url/CommunityFilePage/' + id;
  document.body.appendChild(f);
  setTimeout(() => f.remove(), 1500);
  toast('已请求 Steam 打开创意工坊页面');
}

function groupMenu(e, name) {
  const g = state.groups.find(x => x.name === name);
  showMenu(e.clientX, e.clientY, `
    <div class="title">分组「${esc(name)}」 · ${g ? `${g.enabled}/${g.total} 已启用` : ''}</div>
    <div class="item" data-switch="1">★ 仅启用此组 (其余禁用)</div>
    <div class="item" data-en="1">✔ 启用此组 (追加)</div>
    <div class="item" data-dis="1">✖ 禁用此组</div>
    <div class="sep"></div>
    <div class="item" data-assign="1">＋ 把选中的 mod 加入此组</div>
    <div class="item" data-move="-1">▲ 上移 (提高优先级)</div>
    <div class="item" data-move="1">▼ 下移 (降低优先级)</div>
    <div class="sep"></div>
    <div class="item danger" data-del="1">✖ 删除分组 (mod 不受影响)</div>`);

  const menu = $('ctxmenu');
  const done = () => { menu.hidden = true; };
  menu.querySelector('[data-switch]').onclick = () => { done(); confirmSwitch(name); };
  menu.querySelector('[data-en]').onclick = () => { done(); call('/api/groups/enable', { name }); };
  menu.querySelector('[data-dis]').onclick = () => { done(); call('/api/groups/disable', { name }); };
  menu.querySelector('[data-assign]').onclick = () => {
    done();
    if (!state.selected.size) return toast('请先点选 mod 卡片', 'err');
    call('/api/groups/assign', { name, dirs: [...state.selected] });
  };
  menu.querySelectorAll('[data-move]').forEach(el => el.onclick = () => {
    done(); call('/api/groups/move', { name, delta: Number(el.dataset.move) });
  });
  menu.querySelector('[data-del]').onclick = () => { done(); call('/api/groups/delete', { name }); };
}

function moreMenu(e) {
  showMenu(e.clientX, e.clientY, `
    <div class="title">更多操作</div>
    <div class="item" data-a="import">↓ 导入游戏目录里的实体 mod</div>
    <div class="item" data-a="sync">≫ 一键同步到游戏 (重建链接)</div>
    <div class="sep"></div>
    <div class="item" data-a="lib">▸ 打开 mod 仓库</div>
    <div class="item" data-a="mods">▸ 打开 mods 目录</div>
    <div class="item" data-a="launch">▸ 启动游戏</div>
    <div class="sep"></div>
    <div class="item" data-a="settings">▸ 设置</div>`);
  const menu = $('ctxmenu');
  menu.querySelectorAll('[data-a]').forEach(el => el.onclick = () => {
    menu.hidden = true;
    const a = el.dataset.a;
    if (a === 'import') return call('/api/mods/import', {});
    if (a === 'sync') return call('/api/mods/sync', {});
    if (a === 'lib') return call('/api/open_dir', { which: 'library' });
    if (a === 'mods') return call('/api/open_dir', { which: 'mods' });
    if (a === 'launch') return call('/api/launch', {});
    if (a === 'settings') return openSettings();
  });
}

/* ---------------- 模态 ---------------- */
function modal(html, onMount) {
  $('modalBox').innerHTML = html;
  $('modalRoot').hidden = false;
  $('modalRoot').querySelector('.modal-backdrop').onclick = closeModal;
  onMount && onMount($('modalBox'));
}
function closeModal() { $('modalRoot').hidden = true; }

/* ---------- 退出程序 (exe 无控制台, 这是唯一的正常退出方式) ---------- */
function confirmQuit() {
  return new Promise((resolve) => {
    modal(`<h3>退出程序</h3>
      <div class="row" style="color:var(--fg-soft);line-height:1.7">
        后台服务会一起关闭，这个页面随即失效。<br>
        所有 mod 状态与分组都已存进配置文件，下次打开原样恢复。</div>
      <div class="modal-actions">
        <button class="btn ghost" id="qCancel">取消</button>
        <button class="btn red" id="qOk">退出</button></div>`, (box) => {
      setTimeout(() => box.querySelector('#qOk').focus(), 60);
      box.querySelector('#qCancel').onclick = () => { closeModal(); resolve(false); };
      box.querySelector('#qOk').onclick = () => { closeModal(); resolve(true); };
    });
  });
}

async function quitApp() {
  if (!(await confirmQuit())) return;
  try {
    await fetch('/api/shutdown', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch (e) { /* 服务已关, fetch 报错是正常的 */ }
  document.body.innerHTML =
    `<div style="position:fixed;inset:0;display:grid;place-items:center;text-align:center;
       color:var(--fg-soft);font-size:17px;line-height:2;background:rgba(8,10,18,.92)">
       <div><div style="font-size:40px;margin-bottom:14px">⏻</div>
       程序已退出<div style="font-size:14px;color:var(--fg-dim);margin-top:10px">
       重新使用请再双击 exe / start_manager.bat</div></div></div>`;
}

function promptText(title, placeholder) {
  return new Promise((resolve) => {
    modal(`<h3>${esc(title)}</h3>
      <div class="row"><input type="text" id="mInput" placeholder="${esc(placeholder)}"></div>
      <div class="modal-actions">
        <button class="btn ghost" id="mCancel">取消</button>
        <button class="btn green" id="mOk">确定</button></div>`, (box) => {
      const input = box.querySelector('#mInput');
      setTimeout(() => input.focus(), 60);
      const ok = () => { const v = input.value.trim(); closeModal(); resolve(v); };
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') ok(); });
      box.querySelector('#mOk').onclick = ok;
      box.querySelector('#mCancel').onclick = () => { closeModal(); resolve(''); };
    });
  });
}

function confirmSwitch(name) {
  const g = state.groups.find(x => x.name === name);
  modal(`<h3>切换到分组「${esc(name)}」？</h3>
    <p>将启用该组的 ${g ? g.total : 0} 个 mod，并把其他分组的 mod 全部禁用。
       这只是增删 mods/ 里的链接，<b>实体文件不会被移动或删除</b>。</p>
    <div class="modal-actions">
      <button class="btn ghost" id="mCancel">取消</button>
      <button class="btn green" id="mOk">★ 确认切换</button></div>`, (box) => {
    box.querySelector('#mCancel').onclick = closeModal;
    box.querySelector('#mOk').onclick = () => { closeModal(); call('/api/groups/switch', { name }); };
  });
}

function openSettings() {
  const c = state.cfg;
  modal(`<h3>设置</h3>
    <div class="row"><label>游戏 mods 路径</label>
      <input type="text" id="sMods" value="${esc(c.mods_path || '')}">
      <button class="btn ghost" id="sPickMods">打开</button></div>
    <div class="row"><label>mod 仓库路径</label>
      <input type="text" id="sLib" value="${esc(c.library_path || '')}" placeholder="留空 = 游戏根目录/isaac_mod_library">
      <button class="btn ghost" id="sPickLib">打开</button></div>
    <div class="row"><label>背景不透明度</label>
      <input type="range" id="sBg" min="0" max="100" value="${Math.round((c.bg_opacity ?? .5) * 100)}">
      <span class="val" id="sBgVal">${Math.round((c.bg_opacity ?? .5) * 100)}%</span></div>
    <div class="row"><label>卡片不透明度</label>
      <input type="range" id="sCard" min="0" max="100" value="${Math.round((c.card_opacity ?? .4) * 100)}">
      <span class="val" id="sCardVal">${Math.round((c.card_opacity ?? .4) * 100)}%</span></div>
    <div class="row"><label>显示背景画作</label>
      <label class="check"><input type="checkbox" id="sBgOn" ${c.bg_enabled ? 'checked' : ''}><span>开启</span></label></div>
    <div class="row"><label>数据目录</label>
      <input type="text" id="sData" value="${esc(c.data_dir || '')}" readonly>
      <button class="btn ghost" id="sPickData">打开</button></div>
    <div style="margin:-6px 0 12px;font-size:12px;color:#8b94a8;line-height:1.7">
      配置、分组、Steam 登录凭据都保存在这里（${c.portable ? '当前是<b>便携模式</b>，数据就在程序旁边' : '默认位置 <code>%LOCALAPPDATA%\\IsaacModManager</code>'}）。
      <b>换新版本的 exe 不会影响这些数据</b>；想备份就整个文件夹复制走。</div>
    <div class="modal-actions">
      <button class="btn red" id="sQuit" style="margin-right:auto">退出程序</button>
      <button class="btn ghost" id="sCancel">取消</button>
      <button class="btn green" id="sSave">保存并应用</button></div>`, (box) => {
    const bg = box.querySelector('#sBg'), card = box.querySelector('#sCard');
    const live = () => {
      box.querySelector('#sBgVal').textContent = bg.value + '%';
      box.querySelector('#sCardVal').textContent = card.value + '%';
      document.documentElement.style.setProperty('--bg-opacity', (box.querySelector('#sBgOn').checked ? bg.value / 100 : 0));
      document.documentElement.style.setProperty('--card-alpha', card.value / 100);
    };
    bg.oninput = live; card.oninput = live;
    box.querySelector('#sBgOn').onchange = live;
    box.querySelector('#sPickMods').onclick = () => call('/api/open_dir', { path: box.querySelector('#sMods').value });
    box.querySelector('#sPickLib').onclick = () => call('/api/open_dir', { path: box.querySelector('#sLib').value });
    box.querySelector('#sPickData').onclick = () => call('/api/open_dir', { path: box.querySelector('#sData').value });
    box.querySelector('#sCancel').onclick = closeModal;
    box.querySelector('#sQuit').onclick = () => { closeModal(); quitApp(); };
    box.querySelector('#sSave').onclick = () => {
      closeModal();
      call('/api/settings', {
        mods_path: box.querySelector('#sMods').value.trim(),
        library_path: box.querySelector('#sLib').value.trim(),
        bg_opacity: Number(bg.value) / 100,
        card_opacity: Number(card.value) / 100,
        bg_enabled: box.querySelector('#sBgOn').checked,
      }, '设置已保存');
    };
  });
}

function promptNewGroup() {
  promptText('新建分组', '分组名称（如：忏悔 / 忏悔+）').then((name) => {
    if (name) call('/api/groups/create', { name }, `已创建分组「${name}」`);
  });
}

/* ---------------- 顶部/底栏动作 ---------------- */
function bindChrome() {
  document.querySelectorAll('.btn[data-act], .topbar .btn[data-act]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      ripple(e, btn);
      const a = btn.dataset.act;
      if (a === 'refresh') return refresh();
      if (a === 'import') return call('/api/mods/import', {});
      if (a === 'reset') return resetFilters();
      if (a === 'batch-enable') return batch(true);
      if (a === 'batch-disable') return batch(false);
      if (a === 'more') return moreMenu(e);
    });
  });
  $('btnLibrary').onclick = () => call('/api/open_dir', { which: 'library' });
  $('btnSync').onclick = (e) => { ripple(e, $('btnSync')); call('/api/mods/sync', {}); };
  $('btnSettings').onclick = openSettings;
  $('btnWorkshop').onclick = workshopFromClipboard;
  bindWsPanel();
  // 支持用 #ws=<工坊ID> 直接打开对话框 (可从外部调用, 比如做快捷方式)
  if (location.hash.indexOf('#ws=') === 0) {
    const pid = extractWsId(location.hash.slice(4)) || location.hash.slice(4).replace(/\D/g, '');
    if (pid) setTimeout(() => workshopLookup(pid), 900);
  }
  // #login=1 直接打开登录表单(外部直达, 不必先点工坊按钮)
  if (location.hash === '#login=1' || location.hash === '#login') {
    setTimeout(startWsLogin, 700);
  }
  // #search=关键词 或 #search 直接打开创意工坊搜索
  if (location.hash.indexOf('#search') === 0) {
    const q = location.hash.indexOf('=') > 0 ? location.hash.split('=')[1] : '';
    if (q) { $('wsQuery').value = decodeURIComponent(q); }
    setTimeout(() => openWsSearch(), 700);
  }
  // #settings 直接打开设置(方便做快捷方式 / 截图验证)
  if (location.hash === '#settings') {
    setTimeout(openSettings, 600);
  }
  // #wslist 直接打开「Steam 已下载的工坊 mod」列表
  if (location.hash === '#wslist') {
    setTimeout(workshopSubscribed, 600);
  }
  // #guide 或 #guide=2 直接打开 Mod 入门(可指定章节号)
  if (location.hash.indexOf('#guide') === 0) {
    const n = location.hash.indexOf('=') > 0 ? location.hash.split('=')[1] : '1';
    setTimeout(() => openGuide(n), 600);
  }
  $('btnQuit').onclick = quitApp;
  bindGuide();
  // 「Steam 已下载」: 把原本没有入口的 workshopSubscribed 接出来
  const _wl = $('btnWsList');
  if (_wl) _wl.onclick = workshopSubscribed;
  $('fabLaunch').onclick = () => call('/api/launch', {});
  $('btnClear').onclick = () => { state.selected.clear(); updateSelectionUI(); };
  $('checkAll').onchange = (e) => {
    const list = visibleMods();
    if (e.target.checked) list.forEach(m => state.selected.add(m.dir));
    else list.forEach(m => state.selected.delete(m.dir));
    updateSelectionUI();
  };
  $('pathText').onclick = openSettings;
  $('search').oninput = (e) => {
    state.search = e.target.value;
    $('searchClear').classList.toggle('show', !!state.search);
    renderGrid();
  };
  $('searchClear').onclick = () => {
    state.search = ''; $('search').value = ''; $('searchClear').classList.remove('show'); renderGrid();
  };
  $('filterState').onchange = (e) => { state.filter = e.target.value; renderGrid(); };
  $('sortBy').onchange = (e) => { state.sort = e.target.value; renderGrid(); };
  $('showDisabled').onchange = (e) => { state.showDisabled = e.target.checked; renderGrid(); };
}

function batch(enable) {
  if (!state.selected.size) return toast('请先点选 mod 卡片', 'err');
  call('/api/mods/batch', { dirs: [...state.selected], enable });
  state.selected.clear();
}

function resetFilters() {
  state.search = ''; state.filter = 'all'; state.sort = 'name'; state.showDisabled = true;
  state.view = 'all';
  $('search').value = ''; $('filterState').value = 'all'; $('sortBy').value = 'name';
  $('showDisabled').checked = true; $('searchClear').classList.remove('show');
  render();
  setStatus('已重置筛选条件');
}

/* ---------------- 快捷键 ---------------- */
function bindKeys() {
  // 直接在页面上 Ctrl+V 粘贴工坊链接 → 自动识别并弹窗
  document.addEventListener('paste', (e) => {
    const tag = (e.target && e.target.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    const txt = (e.clipboardData || window.clipboardData).getData('text') || '';
    const pid = extractWsId(txt);
    if (pid) { e.preventDefault(); workshopLookup(pid); }
  });
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeModal(); $('ctxmenu').hidden = true; state.selected.clear(); updateSelectionUI(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
      e.preventDefault();
      visibleMods().forEach(m => state.selected.add(m.dir));
      updateSelectionUI();
    }
    if (e.key === 'F5') { e.preventDefault(); refresh(); }
    if (e.key === 'g' && (e.ctrlKey || e.metaKey) && e.shiftKey) {
      e.preventDefault(); workshopFromClipboard();
    }
    if (e.key === '/' && document.activeElement !== $('search')) { e.preventDefault(); $('search').focus(); }
  });
}

/* ---------------- 启动 ---------------- */
/* URL 参数: ?noblur=1 关毛玻璃, ?bgblur=0 关背景模糊层 (低配/调试用) */
(function () {
  const q = new URLSearchParams(location.search);
  if (q.get('noblur') === '1') document.body.classList.add('no-blur');
  if (q.get('bgblur') === '0') { $('bgBlur').style.display = 'none'; }
})();

initParallax();
initDust();
bindChrome();
bindKeys();
render();
refresh();
setInterval(refresh, 30000);
