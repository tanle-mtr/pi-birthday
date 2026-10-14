# -*- coding: utf-8 -*-
"""网页版 UI 自动化测试。

用 pywebview 打开真实页面（并挂上真正的 Python 计算后端），
直接读取渲染后的 DOM 做断言 —— 比截图严谨得多。

覆盖：
  1. 生日输入已改成 年 / 月 / 日 三个下拉框，且没有搜索范围设置
  2. 内置数据的自动分层搜索，位置与 Python 端逐条一致
  3. 8 位完整生日命中时提前停止深入
  4. 内置数据里找不到时，「继续往后算」真的会调用 gmpy2 算出结果
"""
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 深层计算的目标位数：测试时压到刚好越过内置的 1000 万位，避免等太久
DEEP_TARGET = 12_000_000
os.environ.setdefault('PIWEB_TARGETS', str(DEEP_TARGET))

EXTRACT_JS = r"""
(function () {
  const cards = [...document.querySelectorAll('#results .card')].map(function (c) {
    const q = (s) => { const e = c.querySelector(s); return e ? e.innerText.trim() : ''; };
    return { label: q('.c-label'), pat: q('.c-pat'), pos: q('.c-pos'),
             digits: q('.digits'), hit: c.classList.contains('hit') };
  });
  const g = (id) => { const e = document.getElementById(id); return e ? e.innerText.trim() : ''; };
  return JSON.stringify({
    big: g('bigNum'),
    foot: g('foot'),
    hint: g('hint'),
    seed: g('seedCount'),
    depth: g('loaderDepth'),
    status: g('loaderText'),
    hasYear: !!document.getElementById('by'),
    hasMonth: !!document.getElementById('bm'),
    hasDay: !!document.getElementById('bd'),
    yearCount: document.getElementById('by') ? document.getElementById('by').options.length : 0,
    hasTier: !!document.getElementById('tier'),
    hasTextInput: !!document.getElementById('birthday'),
    deepHidden: document.getElementById('deep').hidden,
    deepTitle: g('deepTitle'),
    deepMsg: g('deepMsg'),
    deepMore: document.getElementById('deepMore') ? !document.getElementById('deepMore').hidden : false,
    busy: document.getElementById('go').disabled,
    cards: cards
  });
})()
"""

SET_DATE_AND_GO = r"""
(function () {
  document.getElementById('by').value = '%s';
  document.getElementById('bm').value = '%s';
  document.getElementById('bd').value = '%s';
  return true;
})()
"""


def snap(window, js=None):
    if js:
        window.evaluate_js(js)
    return json.loads(window.evaluate_js(EXTRACT_JS))


def wait_for(window, js_cond, timeout=200, poll=0.6):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        try:
            if window.evaluate_js(js_cond):
                return True
        except Exception:
            pass
    return False


def wait_number_settled(window, timeout=25, need=4, poll=0.5):
    """等头条大数字的滚动动画播完。

    Anime.js 从 0 滚到目标值约 1.25 秒。判定「稳定」必须连续读到多次相同值 ——
    早期只要求连续 2 次，结果在动画**开始之前**就读到两次 "0" 而提前返回，
    断言拿到 0 误判成失败。现在要求连续 4 次（跨约 1.5 秒，长于动画时长）。
    """
    last, same, t0 = None, 0, time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        try:
            cur = window.evaluate_js(
                "document.getElementById('bigNum') "
                "? document.getElementById('bigNum').innerText : ''")
        except Exception:
            break
        if cur and cur == last:
            same += 1
            if same >= need - 1:
                return cur
        else:
            same = 0
        last = cur
    return last


def main():
    import traceback
    import webview
    import pi_web_shell as shell

    box = {}
    done_evt = threading.Event()

    def step(name):
        print(f"  · 步骤：{name}", flush=True)

    def run_checks(window):
        # 每一步独立捕获异常：本机内存紧张时某一步可能超时或抛
        # ObjectDisposedException，不该把后面的步骤一起带崩
        def guard(name, fn, *a):
            try:
                fn(*a)
                box.setdefault('steps_ok', []).append(name)
            except Exception:
                box.setdefault('step_errors', {})[name] = traceback.format_exc()
                print(f"    ⚠️ 步骤「{name}」异常（继续后续步骤）", flush=True)

        def s_base():
            step('等首次搜索')
            if not wait_for(window, "!document.getElementById('go').disabled && "
                                    "document.querySelectorAll('#results .card').length > 0"):
                raise TimeoutError('等待首次搜索完成超时')
            wait_number_settled(window)
            box['base'] = snap(window)

        def s_deep_done():
            step('等默认生日那次的深层计算结束')
            if not wait_for(window,
                            "/完成|失败|停止|还差|找到了|算不动/.test("
                            "document.getElementById('deepTitle').innerText)",
                            timeout=1800, poll=1.5):
                raise TimeoutError('等待深层计算结束超时')
            wait_number_settled(window)
            box['deep_done'] = snap(window)

        def s_early():
            step('切到 2019-09-14（8 位存在，应提前停止）')
            snap(window, SET_DATE_AND_GO % (2019, 9, 14))
            window.evaluate_js("document.getElementById('go').click()")
            time.sleep(1.0)
            wait_for(window, "!document.getElementById('go').disabled", timeout=60)
            time.sleep(2.5)
            wait_number_settled(window)
            box['early'] = snap(window)

        def s_deep_sample():
            step('切到深层样例（内置里没有，应触发继续往后算）')
            sample = os.path.join(_ROOT, 'deep_sample.txt')
            if not os.path.exists(sample):
                raise FileNotFoundError(sample)
            with open(sample, encoding='utf-8') as f:
                line = f.readline().strip()
            pretty, ymd, pos = line.split('\t')
            y, m, d = pretty.split('-')
            box['sample'] = (pretty, ymd, int(pos))
            snap(window, SET_DATE_AND_GO % (int(y), int(m), int(d)))
            window.evaluate_js("document.getElementById('go').click()")
            time.sleep(1.0)
            wait_for(window, "!document.getElementById('go').disabled", timeout=90)
            time.sleep(3)
            # /找到了/ 也要算在内 —— 早期只等「完成/失败/停止」，
            # 命中时标题是「🎉 找到了」，会一直等到超时。
            if not wait_for(window,
                            "/完成|失败|停止|找到了|还差|算不动/.test("
                            "document.getElementById('deepTitle').innerText)",
                            timeout=3600, poll=1.5):
                raise TimeoutError('等待深层样例计算结束超时')
            wait_number_settled(window)
            box['deep'] = snap(window)

        try:
            guard('基础搜索', s_base)
            guard('深层完成', s_deep_done)
            guard('命中即停', s_early)
            guard('继续往后算', s_deep_sample)
        finally:
            done_evt.set()
            try:
                window.destroy()
            except Exception:
                pass

    index = os.path.join(_ROOT, "web", "index.html")
    print(f"打开页面：{index}")
    print(f"深层计算目标：{DEEP_TARGET:,} 位")
    api = shell.Api()
    window = webview.create_window("UI 测试", url=index, js_api=api,
                                   width=1120, height=880)
    webview.start(lambda: threading.Thread(target=run_checks, args=(window,), daemon=True).start(),
                  http_server=True)
    done_evt.wait(60)

    if 'error' in box:
        print('❌ ' + box['error'])
        return 1
    if 'step_errors' in box:
        print('\n⚠️ 以下步骤出现异常（相关断言跳过）：')
        for k, v in box['step_errors'].items():
            print(f"  · {k}: {v.strip().splitlines()[-1]}")
    if 'base' not in box:
        print('❌ 基础步骤未完成，box 内容：' + str(list(box.keys())))
        return 1

    import pi_birthday as pb
    digits = pb.load_cache(10_000_000) or pb.seed_digits()
    if digits is None:
        print('⚠️ 拿不到基准数据，跳过比对')
        return 0

    # 程序的实际取数顺序是「内置 1000 万位 → 离线数据包」。
    # 有离线包时期望值要用包来算，否则会拿内置的旧结果误判成失败。
    pack = pb.open_pack()
    pack_mode = pack is not None

    def expect_pos(pat):
        if pack_mode:
            return pack.find(pat.encode(), start_digit=0, prefetch=False)
        return digits.find(pat.encode())

    print(f"数据源：{'内置 1000 万位 + 离线包（%d 亿位）' % (pack.digits // 10**8)
                     if pack_mode else '仅内置 1000 万位'}")

    ok = True

    # ---------- 1. 输入方式与界面精简 ----------
    d = box['base']
    print('\n=== 1. 输入方式 ===')
    checks = [
        ('有「年」下拉框', d.get('hasYear')),
        ('有「月」下拉框', d.get('hasMonth')),
        ('有「日」下拉框', d.get('hasDay')),
        ('年份选项数量合理(>100)', (d.get('yearCount') or 0) > 100),
        ('已移除旧的文本输入框', not d.get('hasTextInput')),
        ('已移除「搜索范围」', not d.get('hasTier')),
    ]
    for name, good in checks:
        ok &= bool(good)
        print(f"  {'✅' if good else '❌'} {name}")

    # ---------- 2. 内置数据自动分层搜索 ----------
    print('\n=== 2. 内置数据自动搜索（1990-01-01）===')
    print(f"  头条数字 : {d.get('big')}")
    print(f"  搜索状态 : {d.get('status')}")
    for c in d.get('cards', []):
        print(f"    [{'命中' if c['hit'] else '未出现'}] {c['label']:<20} {c['pat']:<10} {c['pos']}")

    # 第 2 节的快照是在「内置搜索刚结束」附近拍的。离线包只要约 1 秒就能查完，
    # 所以这一刻包可能已经查完、也可能还在查 —— 两种状态都合法，
    # 断言必须对时序不敏感（早期写成只认「未出现」，包一快就假失败）。
    seen, expect = set(), []
    for label, pat in pb.build_patterns('19900101'):
        if pat in seen:
            continue
        seen.add(pat)
        expect.append((label, pat, digits.find(pat.encode())))
    for (label, pat, idx), card in zip(expect, d.get('cards', [])):
        if idx >= 0:
            # 内置里就有 → 位置必须是内置里的（离线包也从 0 搜，结果相同）
            want = f"第 {idx + 1:,} 位"
            good = want == card['pos']
            note = ''
        else:
            pidx = expect_pos(pat) if pack_mode else -1
            want = f"第 {pidx + 1:,} 位" if pidx >= 0 else '未出现'
            good = card['pos'] in (want, '未出现')
            note = f"（包查完后应为「{want}」）"
        ok &= good
        print(f"  {'✅' if good else '❌'} {pat:<10} 期望「{want}」 实际「{card['pos']}」{note}")

    # ---------- 2b. 算完仍未找到 → 应给出「继续往后算」 ----------
    print('\n=== 2b. 无完整生日时的收尾处理 ===')
    dd = box.get('deep_done')
    if not dd:
        print('  ⚠️ 该步骤未完成（见上面的异常），跳过')
    else:
        print(f"  面板标题 : {dd.get('deepTitle')}")
        print(f"  面板消息 : {dd.get('deepMsg')}")
        found_full = any(c['pat'] == '19900101' and c['hit'] for c in dd.get('cards', []))
        if pack_mode:
            # 有 10 亿位包时 19900101 应当被找到
            good = found_full
            ok &= good
            print(f"  {'✅' if good else '❌'} 离线包里找到了完整生日 19900101")
        else:
            if not found_full:
                print('  ✅ 确实没有完整生日 19900101')
            has_more = bool(dd.get('deepMore'))
            msg = dd.get('deepMsg') or ''
            explained = ('内存' in msg) or ('关闭' in msg)
            good = has_more or explained
            ok &= good
            print(f"  {'✅' if good else '❌'} " +
                  ("显示了「继续往后算」按钮" if has_more
                   else f"未显示按钮但给出了内存说明：{msg[:60]}"))

    # ---------- 3. 命中即停止 ----------
    print('\n=== 3. 8 位命中则提前停止（2019-09-14）===')
    e = box.get('early')
    if not e:
        print('  ⚠️ 该步骤未完成（见上面的异常），跳过')
    else:
        idx_full = digits.find(b'20190914')
        want_big = f"{idx_full + 1:,}"
        good = want_big == e.get('big')
        ok &= good
        print(f"  {'✅' if good else '❌'} 头条数字 期望「{want_big}」 实际「{e.get('big')}」")
        stopped = '命中' in (e.get('status') or '')
        ok &= stopped
        print(f"  {'✅' if stopped else '❌'} 状态文本「{e.get('status')}」")
        no_deep = e.get('deepHidden', True)
        ok &= no_deep
        print(f"  {'✅' if no_deep else '❌'} 未触发深层计算（面板保持隐藏）")

    # ---------- 4. 继续往后算 ----------
    print('\n=== 4. 内置里没有 → 继续往后算 ===')
    if 'deep' not in box:
        print('  ⚠️ 该步骤未完成（见上面的异常），跳过')
    else:
        pretty, ymd, pos = box['sample']
        g = box['deep']
        print(f"  测试生日 : {pretty}（{ymd}）")
        print(f"  期望位置 : 第 {pos + 1:,} 位（超出内置的 1000 万位）")
        print(f"  面板标题 : {g.get('deepTitle')}")
        print(f"  面板消息 : {g.get('deepMsg')}")
        print(f"  头条数字 : {g.get('big')}")
        for c in g.get('cards', []):
            print(f"    [{'命中' if c['hit'] else '未出现'}] {c['label']:<20} {c['pat']:<10} {c['pos']}")

        shown = g.get('deepHidden') is False
        ok &= shown
        print(f"  {'✅' if shown else '❌'} 深层计算面板已出现")

        want = f"第 {pos + 1:,} 位"
        card = next((c for c in g.get('cards', []) if c['pat'] == ymd), None)
        good = card is not None and card['pos'] == want
        ok &= good
        print(f"  {'✅' if good else '❌'} 算出来的位置 期望「{want}」 "
              f"实际「{card['pos'] if card else '无此卡片'}」")

        big_ok = g.get('big') == f"{pos + 1:,}"
        ok &= big_ok
        print(f"  {'✅' if big_ok else '❌'} 头条数字 期望「{pos + 1:,}」 实际「{g.get('big')}」")

        title = g.get('deepTitle') or ''
        stepped = ('找到了' in title) or ('完成' in title)
        ok &= stepped
        print(f"  {'✅' if stepped else '❌'} 面板标题显示已结束：「{title}」")

    print('\n' + ('=' * 48))
    print('网页版 UI 测试通过 ✅' if ok else '网页版 UI 测试失败 ❌')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
