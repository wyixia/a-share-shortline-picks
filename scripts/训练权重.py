# 说明：本脚本为可选件（重新训练权重用）。
# 数据目录：环境变量 PICKS_DATA_DIR（默认 F:/aigp/alt_screening），需含历史日K与资金流 jsonl。
# 训练产物会写到本技能的 weights/ 目录。
# 说明：本脚本为可选件（重新训练权重用）。
# 数据目录：环境变量 PICKS_DATA_DIR（默认 F:/aigp/alt_screening），需含历史日K与资金流 jsonl。
# 训练产物会写到本技能的 weights/ 目录。

# -*- coding: utf-8 -*-
"""成交额口径修正后的「重训 + 全面复评」
修正点：成交额按板块取值（科创板688/689 量=股K1；其余 量=手K100）；amt/main1_amt 因子随之修正。
①宽池(只剔ST)流式拟合：10口径各自权重(绝对目标,IS) + 统一目标(三开均值)
②缓存修正合格池面板 panel_v3.pkl
③复评：各口径 IC(IS/OOS)与月同向、全池各口径池均、口径间排名相关、综合方案对比
输出：修正后_重训与复评.html ＋ 权重_修正_10口径IS.json
"""
import os, json, os, pickle, bisect, math, statistics as st
from collections import defaultdict, Counter
import numpy as np

OUT = os.environ.get('PICKS_DATA_DIR', 'F:/aigp/alt_screening')
SKILL_W = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'weights')
snap = json.load(open(OUT + '/close_snap_20260910.json', encoding='utf-8'))
NAME = {c: (v.get('name') or '') for c, v in snap.items()}
SH = {c: v['floatcap'] / v['price'] for c, v in snap.items() if v.get('floatcap') and v.get('price')}
is_star = lambda c: c.startswith(('688', '689'))
strip = lambda c: c[2:] if c[:2] in ('sh', 'sz', 'bj') else c
COMBOS = [('A1open', 'A', 1, 'open'), ('A1close', 'A', 1, 'close'), ('A2open', 'A', 2, 'open'),
          ('A2close', 'A', 2, 'close'), ('A3open', 'A', 3, 'open'), ('A3close', 'A', 3, 'close'),
          ('B2open', 'B', 2, 'open'), ('B2close', 'B', 2, 'close'),
          ('B3open', 'B', 3, 'open'), ('B3close', 'B', 3, 'close')]
KEYS = [c[0] for c in COMBOS]
V5 = ['turn', 'vol3_20', 'fcap', 'close', 'r3x', 'vchg', 'main1', 'main1_amt', 'amt', 'gap_d', 'chg']
SHORT = ['A1开', 'A1收', 'A2开', 'A2收', 'A3开', 'A3收', 'B2开', 'B2收', 'B3开', 'B3收']
OPEN3 = [0, 2, 4]
NF = len(V5) * 2 + 4
SEGS = [('IS', lambda d: d >= '2025-04-23'),
        ('OOS-A', lambda d: '2024-04-01' <= d < '2025-04-23'),
        ('OOS-B', lambda d: d < '2024-04-01')]


def amt_of(r, star):
    if len(r) > 6 and r[6]:
        return r[6]
    return r[5] * (1.0 if star else 100.0) * r[2]


MF = {}
for line in open(OUT + '/backfill_mf_tx_full.jsonl', encoding='utf-8'):
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get('ok') and o.get('mf'):
        MF[strip(o['code'])] = o['mf']
M = defaultdict(dict)
for path in ['backfill_kline_tc.jsonl', 'ws_oos_kline.jsonl', 'fm_raw.jsonl', 'latest_kline.jsonl']:
    p = os.path.join(OUT, path)
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
                if isinstance(e, list) and len(e) >= 6:
                    d, op, cl, hi, lo, v = e[0], float(e[1]), float(e[2]), float(e[3]), float(e[4]), float(e[5])
                else:
                    d = e.get('date'); op, cl = float(e.get('open')), float(e.get('last'))
                    hi, lo, v = float(e.get('high')), float(e.get('low')), float(e.get('volume'))
                if d and op > 0 and cl > 0:
                    M[c][d] = [d, op, cl, hi, lo, v]
            except Exception:
                continue
_cal = sorted({d for m in M.values() for d in m if d >= '2021-09-01'})
CIDX = {d: i for i, d in enumerate(_cal)}
MD = {c: sorted(m) for c, m in M.items()}
print('[1] K线 %d 只 / %d 天' % (len(M), len(_cal)), flush=True)


def pct100(vals):
    vals = np.array(vals, float)
    med = np.nanmedian(vals) if not np.all(np.isnan(vals)) else 0.0
    vals = np.where(np.isnan(vals), med, vals)
    o = np.argsort(vals, kind='stable')
    pr = np.empty(len(vals)); pr[o] = np.arange(len(vals)) / max(len(vals) - 1, 1)
    return pr * 100


def mk_row(c, m, d, k, gi, dlast=None):
    dl = MD[c]
    r0, r1 = m[dl[k - 1]], m[d]
    c0, c1 = r0[2], r1[2]
    if c0 <= 0 or c1 <= 0 or r1[3] == r1[4] or r1[1] <= 0:
        return None
    if 'ST' in NAME.get(c, '').upper():
        return None
    star = is_star(c)
    turn = r1[7] if len(r1) > 7 and r1[7] else None
    if turn is None:
        cnt = r1[5] if star else r1[5] * 100.0
        turn = cnt / SH[c] * 100 if SH[c] > 0 else None
    if turn is None:
        return None
    amt = amt_of(r1, star)
    v3 = st.mean([m[dl[i]][5] for i in range(k - 2, k + 1)])
    v20 = st.mean([m[dl[i]][5] for i in range(k - 21, k - 1)])
    chg = (c1 / c0 - 1) * 100
    row = {'code': c, 'name': NAME.get(c, ''), 'turn': turn,
           'vol3_20': (v3 / v20) if v20 else None, 'fcap': SH[c] * c1, 'close': c1,
           'r3x': (m[dl[k - 1]][2] / m[dl[k - 4]][2] - 1) * 100,
           'vchg': (r1[5] / r0[5] - 1) * 100 if r0[5] > 0 else None,
           'amt': amt, 'gap_d': (r1[1] / c0 - 1) * 100, 'chg': chg,
           'main1_amt': None, 'qual': (turn >= 1.0 and amt >= 1e8),
           'sealed': chg >= (19.7 if star else 9.7) - 0.3}
    mf = MF.get(c, {}).get(d)
    row['main1'] = (mf[0] if mf else None)
    row['main1_amt'] = (mf[0] / amt) if (mf and amt > 0) else None
    return row


CACHE = OUT + '/panel_v3.pkl'
TGT = {('s', ci): (lambda Y, ci=ci: Y[:, ci]) for ci in range(10)}
TGT[('uni', 'open3')] = lambda Y: Y[:, OPEN3].mean(1)
Tacc = {k: [np.zeros((NF, NF)), np.zeros(NF)] for k in TGT}
if os.path.exists(CACHE):
    DAYQ, _wtmp = pickle.load(open(CACHE, 'rb'))
    print('[2] 载入修正面板 v3 %d 天' % len(DAYQ), flush=True)
else:
    DAYQ = []
    for d in _cal:
        gi = CIDX[d]
        if gi + 3 >= len(_cal):
            continue
        wr = []
        for c, m in M.items():
            if c not in SH:
                continue
            dl = MD[c]; k = bisect.bisect_right(dl, d) - 1
            if k < 21 or dl[k] != d:
                continue
            row = mk_row(c, m, d, k, gi)
            if row is None:
                continue
            ya = []; good = True
            for key, ent, j, k2 in COMBOS:
                ed = d if ent == 'A' else _cal[gi + 1]
                ep = m.get(ed); xp = m.get(_cal[gi + j])
                ev_ = (ep[2] if ent == 'A' else ep[1]) if ep else None
                xv = (xp[1] if k2 == 'open' else xp[2]) if xp else None
                if not ev_ or not xv or ev_ <= 0 or xv <= 0:
                    good = False; break
                ya.append((xv / ev_ - 1) * 100)
            if not good:
                continue
            row['y'] = ya
            wr.append(row)
        if len(wr) < 300:
            continue
        Xw = np.zeros((len(wr), NF))
        for fi, f in enumerate(V5):
            pr = pct100([r.get(f) for r in wr])
            Xw[:, fi * 2] = pr; Xw[:, fi * 2 + 1] = 100 - pr
        Xw[:, len(V5) * 2:] = 50
        Yw = np.array([r['y'] for r in wr])
        if d >= '2025-04-23':
            Xc = Xw - Xw.mean(0)
            for k, fn in TGT.items():
                yc = fn(Yw); yc = yc - yc.mean()
                Tacc[k][0].__iadd__(Xc.T @ Xc); Tacc[k][1].__iadd__(Xc.T @ yc)
        qi = [i for i, r in enumerate(wr) if r['qual']]
        if len(qi) < 200:
            continue
        Xq = np.zeros((len(qi), NF), dtype=np.float32)
        for fi, f in enumerate(V5):
            pr = pct100([wr[i].get(f) for i in qi])
            Xq[:, fi * 2] = pr; Xq[:, fi * 2 + 1] = 100 - pr
        Xq[:, len(V5) * 2:] = 50
        DAYQ.append((d, Xq, Yw[qi].astype(np.float32), np.array([wr[i]['sealed'] for i in qi])))
    pickle.dump((DAYQ, None), open(CACHE, 'wb'))
    print('[2] 修正面板 v3 %d 天，池均 %.0f 只' % (len(DAYQ), np.mean([len(x[1]) for x in DAYQ])), flush=True)
    WT = {('s', ci): np.linalg.solve(Tacc[('s', ci)][0] + 1e5 * np.eye(NF), Tacc[('s', ci)][1]) for ci in range(10)}
    WT[('uni', 'open3')] = np.linalg.solve(Tacc[('uni', 'open3')][0] + 1e5 * np.eye(NF), Tacc[('uni', 'open3')][1])
    json.dump({'feats': V5, 'train': '宽池(只剔ST), 绝对目标, IS 2025-04-23~, 修正成交额口径',
               'w': {KEYS[ci]: [float(x) for x in WT[('s', ci)]] for ci in range(10)}},
              open(os.path.join(SKILL_W, '统一10口径.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    np.save(os.path.join(SKILL_W, '统一三开IS.npy'), WT[('uni', 'open3')])
    print('[3] 权重已保存（权重_修正_10口径IS.json / 权重_修正_统一三开IS.npy）', flush=True)
W = {ci: np.array(json.load(open(os.path.join(SKILL_W, '统一10口径.json'), encoding='utf-8'))['w'][KEYS[ci]], float) for ci in range(10)}
WUNI = np.load(OUT + '/权重_修正_统一三开IS.npy')


def ric(s, y):
    rx = np.argsort(np.argsort(s)).astype(float); ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean(); ry -= ry.mean()
    dd = math.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    return float((rx * ry).sum() / dd) if dd > 0 else 0.0


def tstat(v):
    sd = st.pstdev(v)
    return st.mean(v) / (sd / math.sqrt(len(v))) if (len(v) > 2 and sd > 0) else 0.0


# 复评
ICd = {ci: [] for ci in range(10)}
POOL = {ci: [] for ci in range(10)}
for d, X, Y, seal in DAYQ:
    for ci in range(10):
        ICd[ci].append((d, ric(X @ W[ci], Y[:, ci])))
        POOL[ci].append((d, float(Y[:, ci].mean())))
REP = {}
for ci in range(10):
    seg = {sn: [x for d, x in ICd[ci] if cond(d)] for sn, cond in SEGS}
    mon = defaultdict(list)
    for d, x in ICd[ci]:
        mon[d[:7]].append(x)
    REP[ci] = dict(is_=st.mean(seg['IS']), oa=st.mean(seg['OOS-A']), ob=st.mean(seg['OOS-B']),
                   mpos=sum(1 for m in mon if st.mean(mon[m]) > 0), nm=len(mon))
PM = {ci: st.mean([x for d, x in POOL[ci]]) for ci in range(10)}

# 口径间分数相关（Top30 重合）
Msc = np.zeros((10, 10)); Mover = np.zeros((10, 10)); cnt = 0
for d, X, Y, seal in DAYQ:
    S = [X @ W[ci] for ci in range(10)]
    T30 = [set(np.argsort(-S[ci])[:30].tolist()) for ci in range(10)]
    for i in range(10):
        for j in range(10):
            Msc[i, j] += 1.0 if i == j else ric(S[i], S[j])
            Mover[i, j] += 1.0 if i == j else len(T30[i] & T30[j]) / 30.0
    cnt += 1
Msc /= cnt; Mover /= cnt
print('[4] 复评完成', flush=True)


def m2(A, B):
    v = [Msc[i, j] for i in A for j in B if i != j]
    return st.mean(v)


css = ('body{font-family:"Microsoft YaHei",sans-serif;max-width:1180px;margin:18px auto;padding:0 16px;color:#1f2328;line-height:1.5;}'
       'h1{font-size:20px;border-bottom:2px solid #d0d7de;padding-bottom:6px;}'
       'h2{font-size:16px;margin-top:24px;border-left:4px solid #0969da;padding-left:10px;}'
       'table{border-collapse:collapse;margin:8px 0;font-size:11.5px;} th,td{border:1px solid #d0d7de;padding:3px 6px;text-align:center;} th{background:#f6f8fa;}'
       '.pos{color:#cf222e;} .neg{color:#1a7f37;font-weight:bold;} .small{font-size:12px;color:#57606a;}'
       '.verdict{background:#eaf6ff;border:1px solid #b6dbff;border-radius:6px;padding:12px 16px;font-size:13.5px;margin:10px 0;}'
       '.note{background:#fff8e6;border:1px solid #f0d58c;border-radius:6px;padding:8px 12px;font-size:12.5px;margin:8px 0;}')
H = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>修正成交额后重训复评</title><style>%s</style></head><body>' % css]
H.append('<h1>成交额口径修正后 · 重训与复评</h1>')
H.append('<div class="note"><b>修正</b>：成交额按板块取值（科创板量=股K1、其余量=手K100）。'
         '原脚本一律 ×100 → 科创板成交额放大 100 倍、`amt`/`main1_amt` 因子被污染。'
         '本页全部结果基于<b>修正后</b>数据与<b>重新训练</b>的权重（宽池、绝对目标、IS 2025-04~）。</div>')
H.append('<p class="small">合格池（修正）均 %.0f 只/天，%d 天。</p>' % (np.mean([len(x[1]) for x in DAYQ]), len(DAYQ)))

H.append('<h2>表1 各口径排序有效性（自己权重，IS 拟合 → OOS）</h2>')
H.append('<table><tr><th>口径</th><th>IS IC</th><th>OOS-A IC</th><th>OOS-B IC</th><th>月同向</th><th>池均(每次交易 %)</th></tr>')
for ci in range(10):
    r = REP[ci]
    H.append('<tr><td>%s</td><td class="%s">%+.4f</td><td class="%s">%+.4f</td><td class="%s">%+.4f</td>'
             '<td>%d/%d</td><td class="%s">%+.3f</td></tr>'
             % (SHORT[ci], 'pos' if r['is_'] > 0 else 'neg', r['is_'], 'pos' if r['oa'] > 0 else 'neg', r['oa'],
                'pos' if r['ob'] > 0 else 'neg', r['ob'], r['mpos'], r['nm'],
                'pos' if PM[ci] > 0 else 'neg', PM[ci]))
H.append('</table>')

H.append('<h2>表2 口径间排名相关（修正后，分数秩相关 / Top30重合）</h2>')
H.append('<table><tr><th>—</th>' + ''.join('<th>%s</th>' % s for s in SHORT) + '</tr>')
for i in range(10):
    H.append('<tr><th>%s</th>' % SHORT[i])
    for j in range(10):
        H.append('<td>%.2f<br><span class="small">%.0f%%</span></td>' % (Msc[i, j], Mover[i, j] * 100))
    H.append('</tr>')
H.append('</table>')
H.append('<p class="small">上=分数秩相关；下=Top30重合率。开-开均值 %.2f｜收-收 %.2f｜开-收 %.2f。</p>'
         % (m2(OPEN3, OPEN3), m2([1, 3, 5], [1, 3, 5]), m2(OPEN3, [1, 3, 5])))
H.append('</body></html>')
open(OUT + '/修正后_重训与复评.html', 'w', encoding='utf-8').write('\n'.join(H))
print('[5] 已输出 修正后_重训与复评.html', flush=True)
for ci in range(10):
    r = REP[ci]
    print('  %s IS%+.4f OOS-A%+.4f OOS-B%+.4f 月同向%d/%d 池均%+.3f'
          % (SHORT[ci], r['is_'], r['oa'], r['ob'], r['mpos'], r['nm'], PM[ci]))
