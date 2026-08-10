'use strict';
/*
 * 极快 · 块工坊 单页脚本（v0.15.0 W19）。
 *
 * 原生 JS + fetch，无框架、无外链、无构建。三段式：
 *   选块  POST /api/选   → 候选卡片（分数 / 领域 / 层级 / 检索路径 badge）
 *   组装  POST /api/组   → 合成源码（可编辑，带语法着色 + 行号）
 *   跑    POST /api/跑   → {源码, 执行结果}，诊断回显到代码框的行/列
 * 启动时探测 GET /api/能力 决定「神经检索」开关是否可用。
 *
 * 安全基线：服务端 / 执行结果 / 用户自己敲进代码框的文本一律视为不可信。
 * 除高亮层一处 innerHTML（内容在 渲染行 内逐段 esc 后才包白名单 class 的
 * <span>，先转义后插标签，顺序不可颠倒）外，全部走 textContent。
 *
 * 位置口径：后端诊断的 行/列 都是 **1-based 码点**（见 service/schema.py）。
 * JS 的 String.length / 下标是 UTF-16 单元，BMP 内一致、遇 emoji 与扩展区
 * 汉字会偏，所以凡是按列定位的地方都用 Array.from / for..of 按码点走，只在
 * 要喂 setSelectionRange 时才换算回 UTF-16 偏移（见 列转UTF16 / 定位偏移）。
 */

// ---- 小工具 -----------------------------------------------------------

const $ = (id) => document.getElementById(id);

/** HTML 转义。高亮函数产 HTML 前必须先过这一层。 */
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** 统一的 JSON POST。失败抛 Error(中文原因)，由调用方展示。 */
async function postJSON(url, body) {
  let resp;
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    throw new Error('网络请求失败：' + e.message);
  }
  const text = await resp.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch (e) { throw new Error('响应不是合法 JSON（HTTP ' + resp.status + '）'); }
  if (!resp.ok) {
    throw new Error(data && data['错误'] ? data['错误'] : ('HTTP ' + resp.status));
  }
  return data;
}

async function getJSON(url) {
  let resp;
  try { resp = await fetch(url); }
  catch (e) { throw new Error('网络请求失败：' + e.message); }
  const text = await resp.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch (e) { throw new Error('响应不是合法 JSON（HTTP ' + resp.status + '）'); }
  if (!resp.ok) {
    throw new Error(data && data['错误'] ? data['错误'] : ('HTTP ' + resp.status));
  }
  return data;
}

function note(el, msg, isErr) {
  if (!msg) { el.hidden = true; el.textContent = ''; return; }
  el.hidden = false;
  el.textContent = msg;              // textContent：msg 含服务端文案，绝不当 HTML
  el.classList.toggle('err', !!isErr);
}

// ---- 码点 ↔ UTF-16 换算（纯函数，可单测）-----------------------------

/** 按码点切成字符数组（`Array.from` 会正确处理代理对）。 */
function 码点(s) { return Array.from(String(s)); }

/**
 * 行内「1-based 码点列」→「0-based UTF-16 偏移」。
 *
 * 列 = 1 → 偏移 0；列超出行长时钳到行尾（诊断指向 EOL 是常见情形）。
 */
function 列转UTF16(行文本, 列) {
  const cps = 码点(行文本);
  let n = Number(列);
  if (!isFinite(n) || n < 1) n = 1;
  if (n > cps.length + 1) n = cps.length + 1;
  return cps.slice(0, n - 1).join('').length;
}

/** (行,列) → 整份源码里的 UTF-16 绝对偏移，直接喂 setSelectionRange。 */
function 定位偏移(源码, 行, 列) {
  const 行数组 = String(源码).split('\n');
  let r = Number(行);
  if (!isFinite(r) || r < 1) r = 1;
  if (r > 行数组.length) r = 行数组.length;
  let 偏移 = 0;
  for (let i = 0; i < r - 1; i++) 偏移 += 行数组[i].length + 1;   // +1 是 '\n'
  return 偏移 + 列转UTF16(行数组[r - 1], 列);
}

// ---- 极快语法分词（自写，产 token 而不是 HTML）-----------------------
//
// 产 token 列表而不是直接产 HTML，是为了让诊断的「列标记」能按码点切进
// token 中间（HTML 字符串里没法安全地插）。染色规则够用即可：
//   注释 `-- …` 到行尾 / 字符串 「…」 与 "…" / 关键字 / 数字。

const 关键字 = ['从', '导入', '定义', '打印', '返回', '函数', '接收', '如果',
  '否则', '循环', '每个', '在', '中', '为', '真', '假'];

/** 一行 → [{文, 类}]，同类相邻自动合并（也顺手把拆开的代理对拼回去）。 */
function 分词(行) {
  const out = [];
  const 收 = (文, 类) => {
    if (!文) return;
    const 末 = out.length ? out[out.length - 1] : null;
    if (末 && 末.类 === 类) 末.文 += 文; else out.push({ 文: 文, 类: 类 });
  };
  const s = String(行);
  const cm = s.indexOf('--');
  const 代码 = cm >= 0 ? s.slice(0, cm) : s;
  const 注释 = cm >= 0 ? s.slice(cm) : '';
  let i = 0;
  while (i < 代码.length) {
    const ch = 代码[i];
    if (ch === '「' || ch === '"') {
      const 闭 = ch === '「' ? '」' : '"';
      const end = 代码.indexOf(闭, i + 1);
      const j = end < 0 ? 代码.length : end + 1;
      收(代码.slice(i, j), 'tok-str');
      i = j; continue;
    }
    if (ch >= '0' && ch <= '9') {
      let j = i;
      while (j < 代码.length && (代码[j] === '.' || (代码[j] >= '0' && 代码[j] <= '9'))) j++;
      收(代码.slice(i, j), 'tok-num');
      i = j; continue;
    }
    let 命中 = null;
    for (const k of 关键字) { if (代码.startsWith(k, i)) { 命中 = k; break; } }
    if (命中) { 收(命中, 'tok-kw'); i += 命中.length; continue; }
    收(ch, '');
    i += 1;
  }
  if (注释) 收(注释, 'tok-cm');
  return out;
}

/**
 * 一行 → 安全 HTML。`列集合` 是该行需要标记的 1-based 码点列号集合。
 *
 * 逐码点走 token，把「着色类 + 是否被标记」相同的连续码点攒成一段，一段
 * 一次 esc + 包 <span>。class 全部来自本文件的白名单常量，不含外来数据。
 */
function 渲染行(行, 列集合) {
  const toks = 分词(行);
  let html = '', 缓 = '', 缓类 = '', 列号 = 0;
  const 冲 = () => {
    if (!缓) return;
    const 文 = esc(缓);                     // 先转义，再包标签，顺序不能反
    html += 缓类 ? '<span class="' + 缓类 + '">' + 文 + '</span>' : 文;
    缓 = '';
  };
  for (const t of toks) {
    for (const ch of t.文) {                // for..of 按码点迭代
      列号 += 1;
      const 标 = !!(列集合 && 列集合.has(列号));
      const 类 = 标 ? (t.类 ? t.类 + ' col-mark' : 'col-mark') : t.类;
      if (类 !== 缓类) { 冲(); 缓类 = 类; }
      缓 += ch;
    }
  }
  冲();
  // 诊断列越过行尾（指向 EOL / 缺失 token）时补一个可见标记，否则用户
  // 只看到一条空行、不知道错在哪。
  if (列集合) {
    for (const c of 列集合) {
      if (c > 列号) { html += '<span class="col-mark"> </span>'; break; }
    }
  }
  return html;
}

/** 级别 → 白名单 CSS 类。外来字符串只用于查表，绝不拼进 class。 */
function 级别类(级别) {
  if (级别 === '警告') return 'lv-warn';
  if (级别 === '提示') return 'lv-info';
  return 'lv-err';
}

/**
 * 把诊断数组按行归并：行号 → {列集合, 级别}。级别取该行最重的一档
 * （错误 > 警告 > 提示），决定行底色与列标记颜色。
 */
function 归并诊断(诊断) {
  const 表 = new Map();
  const 重 = { '错误': 3, '警告': 2, '提示': 1 };
  (诊断 || []).forEach((d) => {
    if (!d) return;
    let 行 = Number(d['行']);
    if (!isFinite(行) || 行 < 1) 行 = 1;
    let 列 = Number(d['列']);
    if (!isFinite(列) || 列 < 1) 列 = 1;
    let 条 = 表.get(行);
    if (!条) { 条 = { 列: new Set(), 级别: '提示' }; 表.set(行, 条); }
    条.列.add(列);
    const lv = String(d['级别'] || '错误');
    if ((重[lv] || 3) >= (重[条.级别] || 0)) 条.级别 = (lv in 重) ? lv : '错误';
  });
  return 表;
}

/** 整份源码 → 高亮层 HTML（行与源码 1:1，靠 '\n' 分隔，不改变行数）。 */
function 渲染代码(源码, 诊断) {
  const 表 = 归并诊断(诊断);
  return String(源码).split('\n').map((行, i) => {
    const 条 = 表.get(i + 1);
    const 内 = 渲染行(行, 条 ? 条.列 : null);
    if (!条) return 内;
    return '<span class="ln-bad ' + 级别类(条.级别) + '">' + 内 + '</span>';
  }).join('\n');
}

/** 检索路径标签 → badge。取值见 ai/retrieval.py 的 PATH_NEURAL / PATH_HEURISTIC。 */
function 路径标签(路径) {
  const p = String(路径 || '');
  if (p === '[神经]') return { 文: '神经', 类: 'badge b-nn' };
  if (p === '[启发式]') return { 文: '启发式', 类: 'badge b-he' };
  if (!p) return null;
  return { 文: p.replace(/^\[|\]$/g, ''), 类: 'badge' };   // 未知路径原样展示
}

// ---- 状态 -------------------------------------------------------------

/** 块名 → 索引条目（领域数组、导出数组等），用于把候选补成方案步骤。 */
const 块表 = new Map();
/** 当前候选列表（后端返回的协议候选）。 */
let 候选列表 = [];
/** 已选中的候选名称，保持点击顺序 —— 顺序就是方案里的步骤顺序。 */
const 选中 = [];
/** 最近一次 `跑` 拿到的诊断。 */
let 当前诊断 = [];
/** 源码被手工改过 → 诊断的行列不再可信，只降级展示不再画标记。 */
let 诊断失效 = false;
/** `/api/能力` 探测结果：神经开关能不能点。 */
let 神经可用 = false;

// ---- 段二：候选卡片 ---------------------------------------------------

function 渲染候选(cands) {
  候选列表 = cands;
  选中.length = 0;
  const box = $('cands');
  box.textContent = '';              // 清空：不用 innerHTML=''，习惯统一
  if (!cands.length) {
    const p = document.createElement('div');
    p.className = 'muted';
    p.textContent = '没有匹配的块，换个说法试试。';
    box.appendChild(p);
  }
  cands.forEach((c) => box.appendChild(建卡片(c)));
  刷新按钮();
}

/** 建一张候选卡片。用 <button> 以获得原生键盘可达性；字段全走 textContent。 */
function 建卡片(c) {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'card';
  card.setAttribute('aria-pressed', 'false');

  const nm = document.createElement('span');
  nm.className = 'nm';
  nm.textContent = c['名称'];

  const dm = document.createElement('span');
  dm.className = 'badge';
  dm.textContent = String(c['领域'] || '?');

  const lv = document.createElement('span');
  lv.className = 'badge';
  lv.textContent = 'L' + Number(c['层级'] || 0);

  const 路 = 路径标签(c['路径']);
  const sc = document.createElement('span');
  sc.className = 'sc';
  sc.textContent = '分数 ' + Number(c['分数'] || 0).toFixed(4);

  const ds = document.createElement('span');
  ds.className = 'ds';
  ds.textContent = c['描述'] || '';

  card.append(nm, dm, lv);
  if (路) {
    const pb = document.createElement('span');
    pb.className = 路.类;
    pb.textContent = 路.文;
    pb.title = '检索路径';
    card.appendChild(pb);
  }
  card.append(sc, ds);
  card.addEventListener('click', () => 切换选中(c['名称'], card));
  return card;
}

function 切换选中(名称, card) {
  const i = 选中.indexOf(名称);
  if (i >= 0) { 选中.splice(i, 1); card.setAttribute('aria-pressed', 'false'); }
  else { 选中.push(名称); card.setAttribute('aria-pressed', 'true'); }
  刷新按钮();
}

/** 按钮可用性集中一处算，避免各分支各写一遍算漏。 */
function 刷新按钮() {
  const 有源码 = !!$('src').value.trim();
  $('btn-asm').disabled = 选中.length === 0;
  $('btn-copy-plan').disabled = 选中.length === 0;
  $('btn-copy-src').disabled = !有源码;
  $('btn-dl').disabled = !有源码;
  $('btn-run').disabled = !有源码;
}

/** 降级提示条：明显但不阻塞，可关。 */
function 显示降级(说明) {
  const bar = $('degrade');
  bar.textContent = '';
  if (!说明) { bar.hidden = true; return; }
  bar.hidden = false;
  const s = document.createElement('span');
  s.textContent = '⚠ ' + 说明 + '（下面这批候选来自启发式检索）';
  const x = document.createElement('button');
  x.type = 'button';
  x.className = 'x';
  x.textContent = '×';
  x.setAttribute('aria-label', '关闭降级提示');
  x.addEventListener('click', () => { bar.hidden = true; });
  bar.append(s, x);
}

// ---- 候选 → 方案 ------------------------------------------------------

/**
 * 把选中的候选拼成一份协议方案。
 *
 * 步骤字段来自 `/api/blocks` 索引：块=名称、领域=领域[0]、导出名=导出[0]。
 * 这三样都是协议 `步骤` 的必填项，缺一后端 ensure_plan 会拒。
 *
 * `喂数据` 非空时塞成共享量「赵料」并作为每一步的第一个参数 —— 最常见的
 * 「一列数据顺着流过几个块」用法；不填交给后端 --自动链式 的类型图去推。
 */
function 组装方案() {
  const 步骤 = 选中.map((名) => {
    const b = 块表.get(名) || {};
    const 领域 = (b['领域'] && b['领域'][0]) || '数据';
    const 导出 = (b['导出'] && b['导出'][0]) || 名;
    return { '块': 名, '领域': 领域, '导出名': 导出 };
  });
  const 方案 = { '需求': $('q').value.trim(), '步骤': 步骤 };
  const 料 = $('feed').value.trim();
  if (料) {
    方案['共享'] = [{ '名': '赵料', '值': 料 }];
    步骤.forEach((s) => { s['参数'] = ['赵料']; });
  }
  return 方案;
}

// ---- 代码框：高亮 / 行号 / 滚动同步 -----------------------------------

function 刷新代码视图() {
  const src = $('src').value;
  // 唯一的 innerHTML：内容由 渲染代码 → 渲染行 逐段 esc 后再包白名单 class，
  // 不可信文本在这之前已经变成实体，插不进标签。
  $('hl').innerHTML = 渲染代码(src, 诊断失效 ? [] : 当前诊断);
  建行号(src);
  同步滚动();
}

function 建行号(src) {
  const 表 = 诊断失效 ? new Map() : 归并诊断(当前诊断);
  const g = $('gutter');
  g.textContent = '';
  const 行数 = String(src).split('\n').length;
  for (let i = 1; i <= 行数; i++) {
    const d = document.createElement('div');
    const 条 = 表.get(i);
    if (条) d.className = 条.级别 === '错误' ? 'bad' : 'warn';
    d.textContent = String(i);
    g.appendChild(d);
  }
}

/** 高亮层与行号槽自己不滚，滚动位置从 textarea 抄过来。 */
function 同步滚动() {
  const ta = $('src');
  $('hl').scrollTop = ta.scrollTop;
  $('hl').scrollLeft = ta.scrollLeft;
  $('gutter').scrollTop = ta.scrollTop;
}

function 行像素高() {
  const cs = getComputedStyle($('src'));
  const lh = parseFloat(cs.lineHeight);
  if (isFinite(lh) && lh > 0) return lh;
  const fs = parseFloat(cs.fontSize);
  return (isFinite(fs) && fs > 0) ? fs * 1.5 : 20;
}

// ---- 诊断清单 ---------------------------------------------------------

function 渲染诊断(诊断) {
  const box = $('diag');
  box.textContent = '';
  if (!诊断 || !诊断.length) { box.hidden = true; return; }
  box.hidden = false;

  const h = document.createElement('div');
  h.className = 'diag-h';
  h.textContent = '诊断 ' + 诊断.length + ' 条'
    + (诊断失效 ? '　（源码已改动，行列标记已失效，位置仅供参考）' : '　（点一条跳到出错位置）');
  box.appendChild(h);

  诊断.forEach((d) => {
    const it = document.createElement('button');
    it.type = 'button';
    it.className = 'diag-item ' + 级别类(d && d['级别']) + (诊断失效 ? ' stale' : '');

    const pos = document.createElement('span');
    pos.className = 'pos';
    pos.textContent = '行 ' + (Number(d['行']) || 1) + ' 列 ' + (Number(d['列']) || 1);

    const lv = document.createElement('span');
    lv.className = 'lv';
    lv.textContent = String(d['级别'] || '错误');

    const msg = document.createElement('span');
    msg.className = 'msg';
    // 消息 / 代码 都是服务端文本 → textContent
    msg.textContent = (d['代码'] ? '[' + d['代码'] + '] ' : '') + String(d['消息'] || '');

    it.append(pos, lv, msg);
    it.addEventListener('click', () => 跳到(d));
    box.appendChild(it);
  });
}

/** 把光标定到诊断位置：码点列先换算成 UTF-16 偏移，再喂 setSelectionRange。 */
function 跳到(d) {
  const ta = $('src');
  const 起 = 定位偏移(ta.value, d['行'], d['列']);
  ta.focus();
  try { ta.setSelectionRange(起, Math.min(起 + 1, ta.value.length)); }
  catch (e) { /* 极端情况下（值刚被换掉）定位失败不该打断交互 */ }
  let 行 = Number(d['行']);
  if (!isFinite(行) || 行 < 1) 行 = 1;
  ta.scrollTop = Math.max(0, (行 - 3) * 行像素高());
  同步滚动();
}

// ---- 结果展示 ---------------------------------------------------------

/** 展示 `执行结果`（新契约：stdout 是原始字符串，不是按行切好的数组）。 */
function 展示执行(r) {
  const out = $('out');
  out.textContent = '';
  const 段 = (标题, 文本, cls) => {
    if (文本 === undefined || 文本 === null || 文本 === '') return;
    const h = document.createElement('div');
    h.className = 'sec';
    h.textContent = 标题;
    const b = document.createElement('div');
    b.className = 'body' + (cls ? ' ' + cls : '');
    b.textContent = String(文本);     // 执行结果一律 textContent，杜绝 XSS
    out.append(h, b);
  };
  if (r['错误']) 段('错误', r['错误'], 'err');
  const 诊断 = Array.isArray(r['诊断']) ? r['诊断'] : [];
  if (诊断.length) 段('诊断', 诊断.length + ' 条（见左侧代码框标记与清单）');
  段('stdout', r['stdout']);
  段('stderr', r['stderr'], 'err');   // 诊断走 stderr，不显示等于瞎
  段('返回值', r['返回值']);
  const 耗时 = document.createElement('div');
  耗时.className = 'sec';
  耗时.textContent = '耗时 ' + Number(r['耗时毫秒'] || 0).toFixed(1) + ' ms';
  out.appendChild(耗时);
}

// ---- 分享：复制 / 下载 ------------------------------------------------

async function 复制(文本, 什么) {
  if (!文本) return;
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      throw new Error('浏览器未提供剪贴板接口（非 https / 非 localhost？）');
    }
    await navigator.clipboard.writeText(文本);
    note($('run-note'), 什么 + '已复制到剪贴板。');
  } catch (e) {
    note($('run-note'), 什么 + '复制失败：' + e.message + '　请手动选中复制。', true);
  }
}

/** 下载 `.jk`：纯前端 Blob + createObjectURL，不新增后端端点。 */
function 下载jk() {
  const 源 = $('src').value;
  if (!源.trim()) return;
  const blob = new Blob([源], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 文件名();
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 立刻 revoke 会让部分浏览器来不及取数据，挪到下一个任务里回收。
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  note($('run-note'), '已下载 ' + 文件名() + '。');
}

/** 用需求文本当文件名，剔掉路径分隔符与空白（防目录穿越 / 怪文件名）。 */
function 文件名() {
  const 名 = $('q').value.trim().replace(/[\\/:*?"<>|\s.]+/g, '_').slice(0, 24);
  return (名 || '方案') + '.jk';
}

// ---- 三段动作 ---------------------------------------------------------

async function 选块() {
  const q = $('q').value.trim();
  if (!q) { note($('sel-note'), '先说说你想干什么。', true); return; }
  const btn = $('btn-sel');
  btn.disabled = true;
  note($('sel-note'), '检索中…');
  try {
    const 请求 = { '需求': q, 'top': 8 };
    if (神经可用 && $('neural').checked) 请求['神经'] = true;
    const data = await postJSON('/api/选', 请求);
    渲染候选(data['候选'] || []);
    显示降级(data['降级说明'] || '');
    note($('sel-note'), '');
  } catch (e) {
    note($('sel-note'), e.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function 组装() {
  if (!选中.length) return;
  const btn = $('btn-asm');
  btn.disabled = true;
  note($('asm-note'), '组装中…');
  try {
    const data = await postJSON('/api/组', { '方案': 组装方案() });
    $('src').value = data['源码'] || '';
    当前诊断 = [];
    诊断失效 = false;
    刷新代码视图();
    渲染诊断(当前诊断);
    note($('asm-note'), '');
    note($('run-note'), '');
    刷新按钮();
  } catch (e) {
    note($('asm-note'), e.message, true);
  } finally {
    刷新按钮();
  }
}

async function 跑() {
  if ($('btn-run').disabled) return;
  const btn = $('btn-run');
  btn.disabled = true;
  $('out').textContent = '运行中…';
  try {
    // `/api/跑` 吃的是方案不是源码，所以这里仍送当前选中拼出的方案。
    const data = await postJSON('/api/跑', { '方案': 组装方案() });
    const 结果 = data['执行结果'] || {};
    const 回源 = data['源码'];
    // 服务端返回它**实际执行**的源码。用它覆盖代码框，诊断的行列才对得上；
    // 手工改动没被执行这件事必须说出来，不能默默丢掉。
    if (typeof 回源 === 'string' && 回源) {
      const 改过 = $('src').value !== 回源;
      $('src').value = 回源;
      note($('run-note'), 改过
        ? '跑的是方案重新合成的源码；代码框里的手工修改未被执行，已用实际执行的源码覆盖（否则诊断行列对不上）。'
        : '');
    }
    当前诊断 = Array.isArray(结果['诊断']) ? 结果['诊断'] : [];
    诊断失效 = false;
    刷新代码视图();
    渲染诊断(当前诊断);
    展示执行(结果);
  } catch (e) {
    // 4xx/5xx 与网络错误：只有纯文本原因，退化成文本提示
    当前诊断 = [];
    刷新代码视图();
    渲染诊断(当前诊断);
    展示执行({ '错误': e.message, '耗时毫秒': 0 });
  } finally {
    刷新按钮();
  }
}

/** Esc：清空整条流水线，回到初始状态。 */
function 清空() {
  $('q').value = '';
  $('feed').value = '';
  $('src').value = '';
  候选列表 = [];
  选中.length = 0;
  当前诊断 = [];
  诊断失效 = false;
  $('cands').textContent = '';
  显示降级('');
  刷新代码视图();
  渲染诊断(当前诊断);
  $('out').textContent = '';
  const p = document.createElement('span');
  p.className = 'muted';
  p.textContent = '跑一下看看。';
  $('out').appendChild(p);
  note($('sel-note'), '');
  note($('asm-note'), '');
  note($('run-note'), '');
  刷新按钮();
  $('q').focus();
}

// ---- 能力探测 ---------------------------------------------------------

/**
 * `GET /api/能力` → 神经开关点亮/置灰。
 *
 * 这个端点可能还没上线（W20 在加），探测失败一律当「不可用」处理并把原因
 * 写在提示里 —— 绝不让一个 404 把整页搞挂。
 */
async function 探测能力() {
  const box = $('neural');
  const 标签 = $('neural-label');
  const 提示 = $('cap-hint');
  try {
    const cap = await getJSON('/api/能力');
    神经可用 = !!cap['神经可用'];
    const 版本 = cap['索引版本'] ? String(cap['索引版本']) : '?';
    const 块数 = Number(cap['块数'] || 0);
    box.disabled = !神经可用;
    if (神经可用) {
      提示.textContent = '（可用 · 索引 ' + 版本 + ' · ' + 块数 + ' 块）';
      标签.title = '神经检索可用：索引版本 ' + 版本 + '，覆盖 ' + 块数 + ' 块';
    } else {
      box.checked = false;
      提示.textContent = '（不可用 · 走启发式）';
      标签.title = '神经检索不可用：向量索引或 embedding 服务缺失，检索走启发式';
    }
  } catch (e) {
    神经可用 = false;
    box.disabled = true;
    box.checked = false;
    提示.textContent = '（能力探测失败 · 走启发式）';
    标签.title = '探测 /api/能力 失败：' + e.message + '　神经开关暂不可用，检索走启发式';
  }
}

// ---- 事件绑定 ---------------------------------------------------------

/**
 * 全局快捷键：Ctrl/Cmd+Enter 跑，Esc 清空。挂在 document 上，输入框与代码框
 * 里都生效。**输入法组字期间一律放行**（`isComposing`，外加 keyCode 229 兜
 * 老浏览器），否则中文用户按回车上字会被当成提交。
 */
function 全局键(e) {
  if (e.isComposing || e.keyCode === 229) return;
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    if (!$('btn-run').disabled) { e.preventDefault(); 跑(); }
    return;
  }
  if (e.key === 'Escape') { e.preventDefault(); 清空(); }
}

function 绑定() {
  $('btn-sel').addEventListener('click', 选块);
  $('btn-asm').addEventListener('click', 组装);
  $('btn-run').addEventListener('click', 跑);
  $('btn-copy-plan').addEventListener('click', () => {
    复制(JSON.stringify(组装方案(), null, 2), '方案 JSON ');
  });
  $('btn-copy-src').addEventListener('click', () => 复制($('src').value, '源码'));
  $('btn-dl').addEventListener('click', 下载jk);

  // 需求框回车 = 选块，但组字中的回车不算
  $('q').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229
        && !e.ctrlKey && !e.metaKey) { e.preventDefault(); 选块(); }
  });

  const ta = $('src');
  ta.addEventListener('input', () => {
    // 用户一改源码，之前那批诊断的行列就不再指向同一份文本了：标记撤掉，
    // 消息保留（正在照着它改），并在清单头上标明已失效。
    if (当前诊断.length && !诊断失效) { 诊断失效 = true; 渲染诊断(当前诊断); }
    刷新代码视图();
    刷新按钮();
  });
  ta.addEventListener('scroll', 同步滚动);
  document.addEventListener('keydown', 全局键);
}

async function 启动() {
  绑定();
  刷新代码视图();
  探测能力();                          // 不 await：能力探测不该拖慢首屏
  try {
    const idx = await getJSON('/api/blocks');
    (idx['块'] || []).forEach((b) => 块表.set(b['名称'], b));
    $('cand-hint').textContent = '（已载入 ' + 块表.size + ' 个块；点选后组装方案）';
  } catch (e) {
    note($('sel-note'), '载入块索引失败：' + e.message, true);
  }
}

document.addEventListener('DOMContentLoaded', 启动);
