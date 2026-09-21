# -*- coding: utf-8 -*-
"""A股短线选股 · 出每日候选名单（自足脚本）

用法:
    python 出名单.py 2026-09-21            # 可传多个交易日

数据目录:
    环境变量 PICKS_DATA_DIR，未设则默认本包旁的 picks_data/

该目录下需要（由 数据准备.py 生成）:
    close_snap_latest.json       全市场快照（名称/流通股本）
    daily/<交易日>/kline.jsonl   当日全市场 K 线（open/last/high/low/volume/amount/exchange）
    daily/<交易日>/mf.jsonl      当日全市场资金流（MainNetFlow）
    history_kline_<日期>.jsonl   近期历史日 K（算 vol3_20 / r3x / vchg，约 21 个交易日）

输出:
    <数据目录>/候选_<日期>.html   HTML 报告（交付物）
    <数据目录>/候选_<日期>.csv    数据件
"""
import json, os, re, sys, bisect, math, statistics as st, csv
from collections import defaultdict
import numpy as np

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('PICKS_DATA_DIR') or os.path.join(SKILL_DIR, 'picks_data')
TOPN = 30

V5 = ['turn', 'vol3_20', 'fcap', 'close', 'r3x', 'vchg', 'main1', 'main1_amt', 'amt', 'gap_d', 'chg']
NF = len(V5) * 2 + 4

_wj = json.load(open(os.path.join(SKILL_DIR, 'weights', '统一三开.json'), encoding='utf-8'))
WUNI = np.array([_wj['weights'][n] for n in _wj['feature_order']])

_snapf = [p for p in [os.path.join(DATA_DIR, 'close_snap_latest.json'),
                      os.path.join(DATA_DIR, 'close_snap_20260910.json')] if os.path.exists(p)]
if not _snapf:
    print('缺少全市场快照：请先运行 数据准备.py'); sys.exit(1)
snap = json.load(open(_snapf[0], encoding='utf-8'))
NAME = {c: (v.get('name') or '') for c, v in snap.items()}
SH = {c: v['floatcap'] / v['price'] for c, v in snap.items() if v.get('floatcap') and v.get('price')}
is_star = lambda c: c.startswith(('688', '689'))
strip = lambda c: c[2:] if c[:2] in ('sh', 'sz', 'bj') else c

# ---------- 历史 K 线（根目录所有含 ev 的 jsonl + 每日快照，快照优先） ----------
M = defaultdict(dict)
_SOURCES = []
for _f in sorted(os.listdir(DATA_DIR)):
    if not _f.endswith('.jsonl'):
        continue
    if _f.startswith(('history_kline', 'backfill_kline')):
        _SOURCES.append(_f)      # 按命名直接纳入：首行可能是 ok:false 的无bar记录，不能只靠内容探测
        continue
    try:
        _head = ''.join(open(os.path.join(DATA_DIR, _f), encoding='utf-8').readlines()[:20])
    except Exception:
        continue
    if '"ev"' in _head:
        _SOURCES.append(_f)
_SOURCES += ['daily/%s/kline.jsonl' % d for d in sorted(os.listdir(os.path.join(DATA_DIR, 'daily')))
             if os.path.isdir(os.path.join(DATA_DIR, 'daily', d))]
for path in _SOURCES:
    p = os.path.join(DATA_DIR, path)
    if not os.path.exists(p):
        continue
    for line in open(p, encoding='utf-8'):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not o.get('ok'):
            continue
        c = strip(o['code'])
        for e in o.get('ev') or []:
            try:
                if isinstance(e, dict):
                    d = e.get('date'); op = float(e['open']); cl = float(e['last'])
                    hi = float(e['high']); lo = float(e['low']); v = float(e['volume'])
                else:
                    d = e[0]; op = float(e[1]); cl = float(e[2]); hi = float(e[3]); lo = float(e[4]); v = float(e[5])
                if d and op > 0 and cl > 0:
                    M[c][d] = [d, op, cl, hi, lo, v]
            except Exception:
                continue
MD = {c: sorted(m) for c, m in M.items()}
print('[1] 历史K线 %d 只' % len(M), flush=True)


def pct100(vals):
    vals = np.array(vals, float)
    med = np.nanmedian(vals) if not np.all(np.isnan(vals)) else 0.0
    vals = np.where(np.isnan(vals), med, vals)
    o = np.argsort(vals, kind='stable')
    pr = np.empty(len(vals)); pr[o] = np.arange(len(vals)) / max(len(vals) - 1, 1)
    return pr * 100


def run(T):
    kp = os.path.join(DATA_DIR, 'daily', T, 'kline.jsonl')
    mp = os.path.join(DATA_DIR, 'daily', T, 'mf.jsonl')
    if not os.path.exists(kp):
        print('  %s 无当日快照（%s 不存在），请先运行 数据准备.py' % (T, kp)); return
    snapK = {}
    for line in open(kp, encoding='utf-8'):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not o.get('ok'):
            continue
        c = strip(o['code'])
        for e in o.get('ev') or []:
            if e.get('date') == T:
                snapK[c] = e
    MF = {}
    if os.path.exists(mp):
        for line in open(mp, encoding='utf-8'):
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not o.get('ok'):
                continue
            c = strip(o['code'])
            for e in o.get('ev') or []:
                if e.get('date') == T and e.get('MainNetFlow') not in (None, '', '-'):
                    try:
                        MF[c] = float(e['MainNetFlow'])
                    except Exception:
                        continue
    if not snapK:
        print('  %s 快照中无该日 bar，跳过' % T); return
    rows = []
    _st = defaultdict(int)
    for c, e in snapK.items():
        _st['总数'] += 1
        if c not in SH:
            _st['无快照SH'] += 1; continue
        dl = MD.get(c)
        if not dl:
            _st['无历史'] += 1; continue
        k = bisect.bisect_right(dl, T) - 1
        if k < 21:
            _st['历史不足21天'] += 1; continue
        if dl[k] != T:
            _st['T不在历史'] += 1; continue
        m = M[c]
        r0, r1 = m[dl[k - 1]], m[T]
        c0, c1 = r0[2], r1[2]
        if c0 <= 0 or c1 <= 0:
            _st['价格异常'] += 1; continue
        if 'ST' in NAME.get(c, '').upper():
            _st['ST'] += 1; continue
        star = is_star(c)
        turn = float(e['exchange']) if e.get('exchange') else None
        if turn is None:
            cnt = r1[5] if star else r1[5] * 100.0
            turn = cnt / SH[c] * 100 if SH[c] > 0 else None
        if turn is None:
            _st['无换手'] += 1; continue
        amt = float(e['amount']) if e.get('amount') else (r1[5] * (1.0 if star else 100.0) * c1)
        if turn < 1.0 or amt < 1e8:
            _st['流动性不足'] += 1; continue
        _st['合格'] += 1
        v3 = st.mean([m[dl[i]][5] for i in range(k - 2, k + 1)])
        v20 = st.mean([m[dl[i]][5] for i in range(k - 21, k - 1)])
        chg = (c1 / c0 - 1) * 100
        main1 = MF.get(c)
        rows.append({'code': c, 'name': NAME.get(c, ''), 'turn': turn,
                     'vol3_20': (v3 / v20) if v20 else None, 'fcap': SH[c] * c1, 'close': c1,
                     'r3x': (m[dl[k - 1]][2] / m[dl[k - 4]][2] - 1) * 100,
                     'vchg': (r1[5] / r0[5] - 1) * 100 if r0[5] > 0 else None,
                     'amt': amt, 'gap_d': (r1[1] / c0 - 1) * 100, 'chg': chg,
                     'main1': main1,
                     'main1_amt': (main1 / amt) if (main1 is not None and amt > 0) else None,
                     'sealed': chg >= (19.7 if star else 9.7) - 0.3})
    print('  [关卡分布] %s' % dict(_st), flush=True)
    n = len(rows)
    if n == 0:
        print('  合格池 0 只，中止'); return
    X = np.zeros((n, NF), dtype=np.float32)
    for fi, f in enumerate(V5):
        pr = pct100([r.get(f) for r in rows])
        X[:, fi * 2] = pr; X[:, fi * 2 + 1] = 100 - pr
    X[:, len(V5) * 2:] = 50
    s = X @ WUNI
    o = [i for i in np.argsort(-s) if not rows[i]['sealed']][:TOPN]
    fp = os.path.join(DATA_DIR, '候选_%s.csv' % T.replace('-', ''))
    with open(fp, 'w', encoding='utf-8-sig', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['名次', '代码', '名称', '综合分', '当日涨幅%', '换手%', '成交额(亿)', '流通市值(亿)',
                     '主力净流入(万)', 'T日价', '板块'])
        for rnk, i in enumerate(o, 1):
            r = rows[i]
            bd = '科创板' if is_star(r['code']) else ('创业板' if r['code'].startswith('30') else
                 ('北交所' if r['code'][0] in '48' or r['code'].startswith('92') else '主板'))
            wr.writerow([rnk, r['code'], r['name'], round(float(s[i]), 1), round(r['chg'], 2),
                         round(r['turn'], 2), round(r['amt'] / 1e8, 2), round(r['fcap'] / 1e8, 0),
                         round(r['main1'] / 1e4, 0) if r['main1'] is not None else '', r['close'], bd])
    hp = os.path.join(DATA_DIR, '候选_%s.html' % T.replace('-', ''))
    css = ('body{font-family:"Microsoft YaHei",sans-serif;max-width:1080px;margin:18px auto;padding:0 16px;color:#1f2328;line-height:1.5;}'
           'h1{font-size:20px;border-bottom:2px solid #d0d7de;padding-bottom:6px;}'
           '.wrap{overflow-x:auto;} table{border-collapse:collapse;margin:8px 0;font-size:12.5px;min-width:940px;} th,td{border:1px solid #d0d7de;padding:4px 10px;text-align:center;white-space:nowrap;} th{background:#f6f8fa;}'
           '.pos{color:#cf222e;} .neg{color:#1a7f37;font-weight:bold;} .small{font-size:12px;color:#57606a;}'
           '.verdict{background:#eaf6ff;border:1px solid #b6dbff;border-radius:6px;padding:12px 16px;font-size:13.5px;margin:10px 0;}')
    H = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>候选名单 %s</title><style>%s</style></head><body>' % (T, css)]
    H.append('<h1>短线候选名单 · %s</h1>' % T)
    H.append('<div class="verdict"><b>口径</b>：池子 = 剔ST + 换手≥1%% + 成交额≥1亿（%d 只）｜排序 = 综合A权重｜'
             '取前 %d，剔封板｜<b>买入 = 次一交易日开盘，卖出 = 第三日收盘</b>。<br>'
             '<span class="small">T 日数据取自 14:45 生产快照；本表为研究口径名单，与生产报告（weights_v3 + A1open）口径不同。</span></div>'
             % (n, len(o)))
    H.append('<div class="wrap">')
    H.append('<table><tr><th>名次</th><th>代码</th><th>名称</th><th>板块</th><th>当日涨幅%</th><th>换手%</th>'
             '<th>成交额(亿)</th><th>流通市值(亿)</th><th>主力净流入(万)</th><th>T日价</th></tr>')
    for rnk, i in enumerate(o, 1):
        r = rows[i]
        bd = '科创板' if is_star(r['code']) else ('创业板' if r['code'].startswith('30') else
             ('北交所' if r['code'][0] in '48' or r['code'].startswith('92') else '主板'))
        cls = 'pos' if r['chg'] > 0 else 'neg'
        H.append('<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td class="%s">%+.2f</td>'
                 '<td>%.2f</td><td>%.2f</td><td>%.0f</td><td>%s</td><td>%.2f</td></tr>'
                 % (rnk, r['code'], r['name'], bd, cls, r['chg'], r['turn'], r['amt'] / 1e8,
                    r['fcap'] / 1e8, ('%.0f' % (r['main1'] / 1e4)) if r['main1'] is not None else '—',
                    r['close']))
    H.append('</table>')
    H.append('</div>')

    # ---- 批次跟踪：各期名单自选股日收盘起逐日跟踪至最新数据日（含全市场等权基准与超额） ----
    batches = []
    _seen = set()
    for f in sorted(os.listdir(DATA_DIR)):
        m2 = re.match(r'候选_(?:新方案_)?(\d{8})\.csv$', f)
        m3 = None if m2 else re.match(r'候选_(\d{4}-\d{2}-\d{2})\.csv$', f)
        if m2:
            P = '%s-%s-%s' % (m2.group(1)[:4], m2.group(1)[4:6], m2.group(1)[6:]); src = '新方案'
        elif m3:
            P = m3.group(1); src = '生产线'
        else:
            continue
        if P in _seen or P > T:
            continue
        try:
            stocks = [strip(r.get('代码', '')) for r in csv.DictReader(open(os.path.join(DATA_DIR, f), encoding='utf-8-sig'))]
        except Exception:
            continue
        stocks = [c for c in stocks if c and c in M and P in M[c] and M[c][P][2] > 0]
        if len(stocks) >= 10:
            batches.append((P, src, stocks)); _seen.add(P)
    batches.sort()
    H.append('<h2>批次跟踪 · 各期名单自选股日收盘起逐日表现（截至最新数据日，全市场等权为基准）</h2>')
    if batches:
        P0 = batches[0][0]
        dates_all = [d for d in sorted({d for mm in M.values() for d in mm}) if P0 <= d <= T]
        uni = [c for c in M if P0 in M[c] and M[c][P0][2] > 0]
        mkt_lvl = {}
        for d in dates_all:
            vals = [M[c][d][2] / M[c][P0][2] for c in uni if d in M[c] and M[c][d][2] > 0]
            if len(vals) > 500:
                mkt_lvl[d] = st.mean(vals)
        rows_sum = []; curves = {}; labels = []
        for P, src, stocks in batches:
            base = st.mean([M[c][P][2] for c in stocks])
            cur = {}
            for d in dates_all:
                vals = [M[c][d][2] for c in stocks if d in M[c] and M[c][d][2] > 0]
                if len(vals) >= max(10, int(len(stocks) * 0.8)) and base > 0:
                    cur[d] = (st.mean(vals) / base - 1) * 100
            if len(cur) < 2:
                continue
            curves[P] = cur; labels.append('%s(%s)' % (P[5:], src))
            last_d = sorted(cur)[-1]
            mp0 = mkt_lvl.get(P); mkl = mkt_lvl.get(last_d)
            mktv = (mkl / mp0 - 1) * 100 if (mp0 and mkl) else None
            rows_sum.append((P, src, len(stocks), P, last_d, cur[last_d], mktv,
                             (cur[last_d] - mktv) if mktv is not None else None))
        rows_sum = rows_sum[::-1]   # 最新批次在前
        H.append('<div class="wrap">')
        H.append('<table><tr><th>批次(选股日)</th><th>来源</th><th>只数</th><th>基准日</th><th>最新数据日</th>'
                 '<th>区间累计%</th><th>同期全市场%</th><th>超额%</th></tr>')
        for P, src, n2, d0, d1, v, mk, ex in rows_sum:
            cls = 'pos' if v > 0 else 'neg'
            H.append('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td>'
                     '<td class="%s">%+.2f</td><td>%+.2f</td><td class="%s">%+.2f</td></tr>'
                     % (P, src, n2, d0, d1, cls, v, mk if mk is not None else 0,
                        'pos' if (ex or 0) > 0 else 'neg', ex if ex is not None else 0))
        H.append('</table>')
        H.append('</div>')
        mr = st.mean([x[5] for x in rows_sum]) if rows_sum else 0
        mex = st.mean([x[7] for x in rows_sum if x[7] is not None]) if rows_sum else 0
        H.append('<p class="small">共 %d 批：区间累计日均 %+.2f%%、对全市场平均超额 %+.2f%%（逐日盯市，未计成本）。'
                 '名单为候选池，非买入清单。</p>' % (len(rows_sum), mr, mex))
        H.append('<div class="chart" id="chart"></div>')
        H.append('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')
        H.append('<script>if(window.echarts){var c=echarts.init(document.getElementById("chart"));'
                 'c.setOption({tooltip:{trigger:"axis"},legend:{data:%s},'
                 'xAxis:{type:"category",data:%s},yAxis:{type:"value",axisLabel:{formatter:"{value}%%"}},'
                 'series:%s});}else{document.getElementById("chart").innerHTML="（无网络，图表跳过；数据见上表）";}</script>'
                 % (json.dumps(['全市场等权'] + labels), json.dumps(dates_all),
                    json.dumps([{'name': '全市场等权', 'type': 'line', 'data': [mkt_lvl.get(d) - 1 if d in mkt_lvl else None for d in dates_all] and [(mkt_lvl.get(d) - 1) * 100 if d in mkt_lvl else None for d in dates_all]}]
                               + [{'name': lb, 'type': 'line', 'data': [curves[P0b].get(d) for d in dates_all]}
                                  for (P0b, lb) in zip([b[0] for b in batches][::-1], labels[::-1])])))
        H.append('<p class="small" style="margin-top:2px">各批次曲线自其选股日收盘起算（图中前段为空属正常）；全市场等权=全部有行情股票自最早批次日起等权累计。</p>')
    else:
        H.append('<p class="small">暂无可跟踪的历史名单（需数据目录中累积 候选_<日期>.csv，且选股日收盘价已有行情）。</p>')

    # ---- 每批逐股逐日明细（基准=选股日收盘；列为其后每个交易日的收盘价；末列=区间涨幅） ----
    for P, src, stocks in reversed(batches):
        dates_b = [d for d in sorted({d for mm in M.values() for d in mm}) if P <= d <= T]
        rows_px = []
        for c in stocks:
            base = M[c][P][2]
            cells = [M[c].get(d, (None, None, None, None, None, None))[2] for d in dates_b]
            last = next((v for v in reversed(cells) if v), base)
            rows_px.append((NAME.get(c, ''), c, base, cells, (last / base - 1) * 100 if base > 0 else 0))
        rows_px.sort(key=lambda x: -x[4])
        ups = sum(1 for x in rows_px if x[4] > 0); downs = len(rows_px) - ups
        best = rows_px[0]; worst = rows_px[-1]
        H.append('<h3>%s 批 · %s %d 只 · 逐股逐日（基准 %s 收盘，至最新数据日）</h3>' % (P[5:], src, len(stocks), P))
        H.append('<div class="wrap"><table><tr><th>名称</th><th>代码</th><th>基准</th>'
                 + ''.join('<th>%s</th>' % d[5:] for d in dates_b) + '<th>区间涨幅</th></tr>')
        for nm, c, base, cells, ret in rows_px:
            tds = ''.join('<td>%s</td>' % ('%.2f' % v if v else '—') for v in cells)
            H.append('<tr><td>%s</td><td>%s</td><td>%.2f</td>%s<td class="%s">%+.2f</td></tr>'
                     % (nm, c, base, tds, 'pos' if ret > 0 else 'neg', ret))
        H.append('</table></div>')
        H.append('<p class="small">区间最强：<b>%s %+.2f%%</b>；最弱：%s %+.2f%%；%d 涨 %d 跌。收盘价缺失记为“—”。</p>'
                 % (best[0], best[4], worst[0], worst[4], ups, downs))

    H.append('<p class="small">生成时间：%s｜数据目录：%s</p>' % (T, DATA_DIR))
    H.append('</body></html>')
    open(hp, 'w', encoding='utf-8').write('\n'.join(H))
    print('  %s 合格池 %d 只 → Top%d：%s' % (T, n, len(o), '、'.join(rows[i]['name'] for i in o[:8])), flush=True)
    print('  输出 %s' % hp, flush=True)
    print('  输出 %s' % fp, flush=True)


if __name__ == '__main__':
    if not sys.argv[1:]:
        print('用法: python 出名单.py 2026-09-21')
        sys.exit(0)
    for T in sys.argv[1:]:
        run(T)
