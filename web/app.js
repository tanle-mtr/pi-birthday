/* ============================================================
   生日在圆周率第几位？ — 网页版逻辑

   数据分两层：
     1. 内置 1000 万位（pi_seed_10m.z）—— 浏览器原生 DecompressionStream 解压，秒出
     2. 内置里找不到完整生日时，通过 pywebview 的 js_api 回调 Python，
        用 gmpy2 多进程**继续往后算**（离线，只回传位置与上下文，不回传上亿位数字）

   动画：Anime.js
   ============================================================ */

const SEED_URL = 'pi_seed_10m.z';
const PI_HEAD = '1415926535897932';

/* 内置数据的搜索层，由浅入深 */
const STAGES = [100_000, 1_000_000, 10_000_000];
const STAGE_PAUSE = 190;

let DIGITS = '';
let ready = false;
let busy = false;

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ============================================================
   生日选择：年 / 月 / 日 三个下拉框
   ============================================================ */

function fillDateSelectors() {
  const by = $('by'), bm = $('bm'), bd = $('bd');
  const now = new Date().getFullYear();

  for (let y = now; y >= 1900; y--) by.add(new Option(y + ' 年', y));
  for (let m = 1; m <= 12; m++) bm.add(new Option(m + ' 月', m));

  by.value = 1990;
  bm.value = 1;
  refreshDays();

  by.addEventListener('change', refreshDays);
  bm.addEventListener('change', refreshDays);
  [by, bm, bd].forEach((el) => el.addEventListener('change', () => { if (ready) onGo(); }));
}

/* 按年/月重建「日」列表：自动处理大小月与闰年 */
function refreshDays() {
  const y = +$('by').value;
  const m = +$('bm').value;
  const bd = $('bd');
  const keep = +bd.value || 1;
  const n = new Date(y, m, 0).getDate();     // 当月天数
  bd.innerHTML = '';
  for (let d = 1; d <= n; d++) bd.add(new Option(d + ' 日', d));
  bd.value = Math.min(keep, n);
}

function readDate() {
  const y = String(+$('by').value).padStart(4, '0');
  const mm = String(+$('bm').value).padStart(2, '0');
  const dd = String(+$('bd').value).padStart(2, '0');
  return { ymd: y + mm + dd, y, mm, dd, yy: y.slice(2) };
}

/* ============================================================
   载入内置数据
   ============================================================ */

async function loadDigits() {
  const bar = $('loaderFill');
  const txt = $('loaderText');

  if (typeof DecompressionStream === 'undefined') {
    throw new Error('当前浏览器不支持 DecompressionStream，无法解压内置数据');
  }

  const res = await fetch(SEED_URL);
  if (!res.ok) throw new Error(`读取内置数据失败：HTTP ${res.status}`);

  const total = Number(res.headers.get('Content-Length')) || 0;
  const reader = res.body.pipeThrough(new DecompressionStream('deflate')).getReader();
  const decoder = new TextDecoder('ascii');

  let out = '';
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    got += value.length;
    out += decoder.decode(value, { stream: true });
    if (total) {
      bar.style.width = Math.min(100, (got / (total * 2.12)) * 100).toFixed(1) + '%';
      txt.textContent = `正在解压内置圆周率数据… ${(got / 1048576).toFixed(1)} MB`;
    }
  }
  out += decoder.decode();

  if (!out.startsWith(PI_HEAD)) throw new Error('内置数据校验失败：开头不是 3.14159265358979…');

  bar.style.width = '100%';
  txt.textContent = `就绪 · 已装载 ${out.length.toLocaleString()} 位圆周率`;
  $('loaderDepth').textContent = '';
  DIGITS = out;
  $('seedCount').textContent = (out.length / 10000).toFixed(0) + ' 万';
  return out.length;
}

/* ============================================================
   模式构造 / 查找
   ============================================================ */

function buildPatterns(b) {
  const list = [
    ['完整生日', 'YYYYMMDD', b.ymd],
    ['年月日',   'YYMMDD',   b.yy + b.mm + b.dd],
    ['月日年',   'MMDDYY',   b.mm + b.dd + b.yy],
    ['日月年',   'DDMMYY',   b.dd + b.mm + b.yy],
    ['月日',     'MMDD',     b.mm + b.dd],
  ];
  const seen = new Set();
  return list.filter(([, , pat]) => (seen.has(pat) ? false : (seen.add(pat), true)));
}

function context(idx, len, span = 26) {
  const a = Math.max(0, idx - span);
  const b = Math.min(DIGITS.length, idx + len + span);
  return { left: DIGITS.slice(a, idx), mid: DIGITS.slice(idx, idx + len), right: DIGITS.slice(idx + len, b) };
}

function locateAll(b) {
  return buildPatterns(b).map(([label, fmt, pat]) => {
    const idx = DIGITS.indexOf(pat);
    return { label, fmt, pat, idx, end: idx < 0 ? -1 : idx + pat.length };
  });
}

function rowsAt(abs, depth) {
  return abs.map((r) => ({
    label: r.label, fmt: r.fmt, pat: r.pat,
    idx: (r.idx >= 0 && r.end <= depth) ? r.idx : -1,
  }));
}

function setDepth(depth, cap, done) {
  $('loaderFill').style.width = (cap ? Math.min(100, (depth / cap) * 100) : 0).toFixed(1) + '%';
  $('loaderText').textContent = done
    ? `已在内置数据第 ${depth.toLocaleString()} 位命中`
    : `自动往后搜索中…已搜到第 ${depth.toLocaleString()} 位`;
  $('loaderDepth').textContent = `${depth.toLocaleString()} 位`;
}

/* 内置数据：由浅入深 */
async function progressiveSearch(b) {
  const cap = DIGITS.length;
  const abs = locateAll(b);
  const stages = STAGES.filter((s) => s < cap);
  stages.push(cap);

  let lastRows = null;
  for (let i = 0; i < stages.length; i++) {
    const depth = stages[i];
    const rows = rowsAt(abs, depth);
    setDepth(depth, cap, false);
    render(b, rows, depth);
    lastRows = rows;
    if (rows.some((r) => r.fmt === 'YYYYMMDD' && r.idx >= 0)) {
      setDepth(depth, cap, true);
      break;
    }
    if (i < stages.length - 1) await sleep(STAGE_PAUSE);
  }
  return { rows: lastRows, depth: stages[stages.length - 1] };
}

/* ============================================================
   继续往后算：调用 Python（gmpy2 多进程）
   ============================================================ */

function pyApi() {
  return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
}

function showDeep(title) {
  $('deep').hidden = false;
  $('deep').classList.remove('done', 'error');
  $('deepTitle').textContent = title;
  $('deepMsg').textContent = '';
  $('deepStop').hidden = false;
}

function renderSteps(level, levels, doneAll) {
  const box = $('deepSteps');
  if (box.childElementCount !== levels) {
    box.innerHTML = Array.from({ length: levels }, () => '<i></i>').join('');
  }
  [...box.children].forEach((el, i) => {
    el.className = (i + 1 < level) || doneAll ? 'done' : (i + 1 === level ? 'active' : '');
  });
}

/* 继续往后算。返回 {rows, depth} —— 必须把结果回传给调用方，
   因为 JS 里给函数参数重新赋值不会影响外层变量；
   早期版本就因此在返回后又用旧数据渲染了一遍，把正确结果覆盖掉了。

   minDigits：跳过已经算过的级，用于用户点「继续往后算」。 */
async function runDeep(b, rows, minDigits = 0) {
  const api = pyApi();
  if (!api) {
    // 用浏览器直接打开（没有 Python 后端）时的降级提示
    showDeep('未连接计算后端');
    $('deep').classList.add('done');
    $('deepStop').hidden = true;
    $('deepMore').hidden = true;
    renderSteps(1, 1, true);
    $('deepMsg').textContent =
      '当前是用浏览器直接打开的，没有 Python 计算后端，只能搜内置的 1000 万位。' +
      '请运行 PiBirthdayWeb.exe（或 python pi_web_shell.py）以获得「继续往后算」能力。';
    return { rows, depth: 0 };
  }

  let info = {};
  try { info = await api.info(minDigits); } catch (e) { /* ignore */ }

  // 没有 gmpy2 就别开始：纯 Python 回退慢约 1000 倍，只会让人以为卡死了
  if (info && info.gmpy2 === false) {
    showDeep('计算模块 gmpy2 不可用');
    $('deep').classList.add('error');
    $('deepStop').hidden = true;
    $('deepMore').hidden = true;
    renderSteps(1, 1, true);
    $('deepMsg').textContent =
      '缺少 gmpy2，无法继续往后算（纯 Python 回退方案慢约 1000 倍，' +
      '不适合上千万位的计算）。请使用完整的 PiBirthdayWeb.exe。';
    return { rows, depth: 0 };
  }

  const perDigit = info.per_digit_bytes || 13;
  const estMB = (n) => Math.round((20 * 1048576 + perDigit * n) / 1048576);
  const minLabel = minDigits ? `（接着 ${(minDigits / 10000).toFixed(0)} 万往下）` : '';
  const pack = info.pack;

  const targets = (info && info.targets) || [];

  // 有离线数据包：先把它查完（10 亿位，几十秒），查不到再退回本地计算
  if (pack) {
    const yi = (pack.digits / 1e8).toFixed(0);
    showDeep(`先查离线数据包：${yi} 亿位${minLabel}`);
    $('deepStop').hidden = false;
    $('deepMore').hidden = true;
    $('deepMsg').textContent =
      `${info.pack_workers || 4} 线程并行解压+搜索（按块序取结果，保证是首次出现）· ` +
      `每块 ${(pack.chunk / 10000).toFixed(0)} 万位 · 包体 ${pack.size_mb} MB · ` +
      `内存占用与总位数无关`;
    renderSteps(0, Math.max(1, Math.ceil(pack.digits / pack.chunk / 50)), false);
  } else if (!targets.length) {
    const cap = info.max_digits || 0;
    showDeep('可用内存不足，暂时算不动了');
    $('deep').classList.add('error');
    $('deepStop').hidden = true;
    $('deepMore').hidden = false;             // 关掉一些程序后可以再试
    renderSteps(1, 1, true);
    $('deepMsg').textContent =
      `当前可用内存 ${info.avail_mb ? info.avail_mb + ' MB' : '过低'}，` +
      `按「内存优先」策略（单进程，约 ${perDigit} 字节/位）` +
      `最多只能算到 ${cap ? cap.toLocaleString() + ' 位' : '内置数据上限'}，` +
      `已无更大的安全目标。请关闭一些程序后点「继续往后算」。`;
    return { rows, depth: minDigits || 0 };
  } else {
    const planTxt = targets.map((t) => (t / 10000).toFixed(0) + '万').join(' → ');
    showDeep(`继续往后算${minLabel}：${planTxt} 位`);
    $('deepStop').hidden = false;
    $('deepMore').hidden = true;
    $('deepMsg').textContent =
      `没有离线数据包，改用本地计算 · 单进程省内存模式 · ` +
      `最大一级预计峰值 ${estMB(targets[targets.length - 1])} MB` +
      (info.avail_mb ? ` / 可用 ${info.avail_mb} MB` : '') +
      ` · 每 1000 万位约 ${Math.round(perDigit * 1e7 / 1048576)} MB`;
    renderSteps(0, targets.length, false);
  }

  const patterns = buildPatterns(b).map(([label, fmt, pat]) => [label, fmt, pat]);
  let started;
  try {
    started = await api.deep_search(patterns, null, minDigits);
  } catch (e) {
    started = { ok: false, msg: String(e) };
  }
  if (!started || !started.ok) {
    $('deep').classList.add('error');
    $('deepMsg').textContent = '无法启动计算：' + ((started && started.msg) || '未知错误');
    return { rows, depth: minDigits };
  }

  $('deepStop').onclick = () => api.deep_stop();

  let depth = minDigits || 0;
  for (;;) {
    await sleep(700);
    let st;
    try { st = await api.deep_status(); } catch (e) { continue; }

    renderSteps(st.level || 0, targets.length, !!st.done);
    const el = st.elapsed ? ` · 已用 ${Math.round(st.elapsed)} 秒` : '';
    $('deepMsg').textContent = (st.message || '') + el;

    // 每一级算完就先合并一次，让用户看到进展
    if (st.results) {
      rows = mergeDeep(rows, st);
      if (st.depth) depth = st.depth;
    }

    if (!st.running) {
      $('deepStop').hidden = true;
      const found = isFound(rows, b);
      $('deep').classList.toggle('error', !!st.error && !found);
      $('deep').classList.toggle('done', !st.error || found);
      if (found) {
        $('deepTitle').textContent = '🎉 找到了';
        $('deepMore').hidden = true;
      } else if (st.error) {
        $('deepTitle').textContent = '计算中断';
        $('deepMore').hidden = !st.can_continue;   // 中断了也可以接着来
      } else if (st.stopped) {
        $('deepTitle').textContent = '已停止';
        $('deepMore').hidden = false;
      } else {
        $('deepTitle').textContent = '还差一点';
        $('deepMore').hidden = !st.can_continue;
      }
      // 「继续往后算」：用当前深度作为起点，重新按内存规划更大的目标
      $('deepMore').onclick = () => runDeep(b, rows, depth || minDigits);
      render(b, rows, depth || DIGITS.length);
      return { rows, depth: depth || DIGITS.length };
    }
  }
}

/* 完整生日（8 位）是否已经命中 */
function isFound(rows, b) {
  const r = rows.find((x) => x.pat === b.ymd);
  return !!(r && r.idx >= 0);
}

/* 把 Python 算出来的位置合并进当前结果 */
function mergeDeep(rows, st) {
  if (!st.results) return rows;
  return rows.map((r) => {
    const d = st.results[r.pat];
    if (d && d.idx >= 0) {
      return { label: r.label, fmt: r.fmt, pat: r.pat, idx: d.idx, pre: d.pre, ctx: d.ctx };
    }
    return r;
  });
}

/* ============================================================
   渲染
   ============================================================ */

function pickHeadline(rows) {
  const hits = rows.filter((r) => r.idx >= 0);
  if (!hits.length) return null;
  return hits.reduce((a, b) => (b.pat.length > a.pat.length ? b : a));
}

function cardDigitsHtml(r) {
  if (r.ctx != null && r.ctx !== '') {
    const pre = r.pre || 0;
    const left = r.ctx.slice(0, pre);
    const mid = r.ctx.slice(pre, pre + r.pat.length);
    const right = r.ctx.slice(pre + r.pat.length);
    return `${left}<b>${mid}</b>${right}`;
  }
  const c = context(r.idx, r.pat.length);
  return `${c.left}<b>${c.mid}</b>${c.right}`;
}

function render(b, rows, depth) {
  $('results').hidden = false;

  const head = pickHeadline(rows);
  const verdict = $('verdict');
  if (head) {
    // 直接把最终数字写进 DOM，并把目标值存到 data-target。
    // 早期这里写死 "0" 交给动画去填 —— 一旦动画没跑（例如
    // runToken 变化导致提前 return），界面就永远停在 0 上。
    verdict.innerHTML =
      `<div class="v-label">你的生日片段</div>` +
      `<div class="v-big" id="bigNum" data-target="${head.idx + 1}">` +
      `${(head.idx + 1).toLocaleString()}</div>` +
      `<div class="v-sub">位 · 圆周率小数的第 <span class="v-pattern">${head.pat}</span>` +
      ` 次出现（${head.label}）</div>`;
  } else {
    verdict.innerHTML =
      `<div class="v-label">还没找到</div>` +
      `<div class="v-big" id="bigNum">—</div>` +
      `<div class="v-sub">已搜到第 ${depth.toLocaleString()} 位，暂未出现完整生日 ${b.ymd}</div>`;
  }

  $('cards').innerHTML = rows.map((r) => {
    if (r.idx >= 0) {
      return `<div class="card hit" data-card>
        <div class="c-label">${r.label} <span style="opacity:.6">${r.fmt}</span></div>
        <div class="c-pat">${r.pat}</div>
        <div class="c-pos">第 ${(r.idx + 1).toLocaleString()} 位</div>
        <div class="digits">${cardDigitsHtml(r)}</div>
      </div>`;
    }
    return `<div class="card miss" data-card>
      <div class="c-label">${r.label} <span style="opacity:.6">${r.fmt}</span></div>
      <div class="c-pat">${r.pat}</div>
      <div class="c-pos">未出现</div>
    </div>`;
  }).join('');

  $('foot').textContent = depth
    ? `搜索深度：前 ${depth.toLocaleString()} 位 · 位置从 1 起算（不含整数部分的 3）`
    : '';
  return head;
}

function animateResults(head) {
  if (typeof anime === 'undefined') return;
  anime({ targets: '#results [data-card]', opacity: [0, 1], translateY: [16, 0],
          delay: anime.stagger(58), duration: 560, easing: 'cubicBezier(.22,.9,.28,1)' });

  const big = $('bigNum');
  if (head && big) {
    // 目标值来自 DOM（render 已经写好最终值），动画只是「从 0 滚上去」的增强：
    // 即使动画被打断，界面上的数字也始终是正确的
    const target = parseInt(big.dataset.target || '0', 10);
    if (target > 0) {
      const obj = { v: 0 };
      anime({ targets: obj, v: target, duration: 1250, easing: 'easeOutExpo',
              update() { big.textContent = Math.round(obj.v).toLocaleString(); },
              complete() { big.textContent = target.toLocaleString(); } });
    }
  }
  anime({ targets: '#results .digits b',
          boxShadow: ['0 0 20px -3px rgba(34,211,238,.85)', '0 0 30px 2px rgba(139,92,246,.95)'],
          direction: 'alternate', loop: true, duration: 1500, easing: 'easeInOutSine' });
}

/* ============================================================
   交互
   ============================================================ */

function setBusy(on) {
  busy = on;
  const btn = $('go');
  btn.disabled = on || !ready;
  btn.classList.toggle('busy', on);
  btn.querySelector('.btn-label').textContent = on ? '搜索中…' : (ready ? '开始查找' : '装载中…');
}

let runToken = 0;      // 防止快速连点时旧任务把新结果覆盖掉

async function onGo() {
  if (!ready) return;
  const my = ++runToken;
  const b = readDate();

  setBusy(true);
  $('deep').hidden = true;
  $('loader').classList.remove('done');
  $('loaderFill').style.width = '0%';

  const t0 = performance.now();
  const { rows: baseRows, depth } = await progressiveSearch(b);
  if (my !== runToken) return;

  let rows = baseRows;
  let finalDepth = depth;
  const full = rows.find((r) => r.fmt === 'YYYYMMDD');
  const foundFull = full && full.idx >= 0;

  $('loader').classList.add('done');
  let head = render(b, rows, finalDepth);
  animateResults(head);
  setBusy(false);

  const dt = performance.now() - t0;
  $('hint').textContent = foundFull
    ? `${b.y}-${b.mm}-${b.dd} · 完整生日就在内置数据第 ${(full.idx + 1).toLocaleString()} 位！` +
      `实耗 ${dt.toFixed(0)} 毫秒`
    : `${b.y}-${b.mm}-${b.dd} · 内置 ${DIGITS.length.toLocaleString()} 位里没有完整生日，` +
      `继续往后算（本机 gmpy2，不联网）…`;

  if (!foundFull) {
    // 继续往后算；期间结果会随每一级完成而刷新
    const res = await runDeep(b, rows);
    if (my !== runToken) return;
    if (res) { rows = res.rows; finalDepth = res.depth || finalDepth; }
    const after = render(b, rows, finalDepth);
    animateResults(after);
  }
}

/* 鼠标聚光 */
function initSpotlight() {
  const panel = $('panel');
  panel.addEventListener('pointermove', (e) => {
    const r = panel.getBoundingClientRect();
    panel.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100) + '%');
    panel.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100) + '%');
  });
}

function entrance() {
  if (typeof anime === 'undefined') return;
  anime.timeline({ easing: 'cubicBezier(.22,.9,.28,1)' })
    .add({ targets: '.badge', opacity: [0, 1], translateY: [14, 0], duration: 620 })
    .add({ targets: 'h1', opacity: [0, 1], translateY: [22, 0], duration: 760 }, '-=420')
    .add({ targets: '.sub', opacity: [0, 1], translateY: [14, 0], duration: 620 }, '-=520')
    .add({ targets: '#panel', opacity: [0, 1], translateY: [26, 0], scale: [.985, 1], duration: 780 }, '-=460')
    .add({ targets: '#hint', opacity: [0, 1], duration: 600 }, '-=420');
}

/* ============================================================
   启动
   ============================================================ */

(async function boot() {
  initSpotlight();
  entrance();
  fillDateSelectors();

  $('go').addEventListener('click', onGo);
  setBusy(false);

  try {
    await loadDigits();
    ready = true;
    setBusy(false);
    $('loader').classList.add('done');
    // 等 pywebview 桥就绪后再自动跑一次（没有桥也能跑，只是不能继续往后算）
    setTimeout(() => onGo(), 300);
  } catch (e) {
    $('loaderText').textContent = '❌ ' + e.message;
    $('loaderText').style.color = '#f43f5e';
    $('go').disabled = true;
  }
})();
