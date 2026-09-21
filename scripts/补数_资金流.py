# -*- coding: utf-8 -*-
"""腾讯口径资金流全字段回补（westock CLI，批量50只，2021-09-01~2026-09-16）
输出：backfill_mf_tx_full.jsonl
{code, ok, mf:{date:[main, jumbo, block, mainIn, mainOut, mid, retailIn, retailOut, small]}}（单位元）
字段名：main=MainNetFlow, jumbo=JumboNetFlow, block=BlockNetFlow,
        mainIn=MainInFlow, mainOut=MainOutFlow, mid=MidNetFlow,
        retailIn=RetailInFlow, retailOut=RetailOutFlow, small=SmallNetFlow
"""
import os, json, os, time, subprocess
DATA_DIR = os.environ.get('PICKS_DATA_DIR')
if not DATA_DIR:
    raise SystemExit('[!] 未设置数据目录。请先设置环境变量 PICKS_DATA_DIR 指向你的数据目录\n'                     '    （需含 daily/<交易日>/kline.jsonl、历史日K jsonl、close_snap_*.json，详见 README）。\n'                     '    数据可从零回补：先跑 scripts/补数_历史K线.py 与 scripts/补数_资金流.py。')
SKILL_W = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'weights')
NODE = os.environ.get('NODE_BIN', 'node')  # 默认取 PATH 里的 node，也可用环境变量 NODE_BIN 指定
CLI = OUT + '/westock_pkg/package/index.js'
OUTF = OUT + '/backfill_mf_tx_full.jsonl'
D0, D1 = '2021-09-01', '2026-09-16'
FIELDS = [('MainNetFlow', 0), ('JumboNetFlow', 1), ('BlockNetFlow', 2), ('MainInFlow', 3),
          ('MainOutFlow', 4), ('MidNetFlow', 5), ('RetailInFlow', 6), ('RetailOutFlow', 7),
          ('SmallNetFlow', 8)]

codes = [l.strip() for l in open(OUT + '/_all_codes.txt', encoding='utf-8') if l.strip()]
done = set()
if os.path.exists(OUTF):
    for line in open(OUTF, encoding='utf-8'):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get('ok'):
            done.add(o['code'])
todo = [c for c in codes if c not in done]
print('全市场 %d，已回补 %d，待补 %d' % (len(codes), len(done), len(todo)), flush=True)


def parse_md(text):
    idx = None; res = {}
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if idx is None:
            if 'date' in cells and ('symbol' in cells or 'code' in cells):
                idx = {c: i for i, c in enumerate(cells)}
            continue
        if all(set(c) <= set('-: ') for c in cells):
            continue
        try:
            code = cells[idx['symbol'] if 'symbol' in idx else idx['code']]
        except Exception:
            continue
        res.setdefault(code, []).append(cells)
    return idx, res


fw = open(OUTF, 'a', encoding='utf-8')
t0 = time.time(); nb = 0; okc = 0
for i in range(0, len(todo), 50):
    ch = todo[i:i + 50]
    nb += 1
    try:
        r = subprocess.run([NODE, CLI, 'fund', 'flow', ','.join(ch), '--start', D0, '--end', D1],
                           capture_output=True, timeout=300)
        idx, got = parse_md(r.stdout.decode('utf-8', 'ignore'))
        if idx is None:
            idx = {}
    except Exception as e:
        print('批 %d 异常 %s' % (nb, type(e).__name__), flush=True)
        got = {}
        idx = {}
    ni = 0
    for c in ch:
        rows = None
        for key, v in got.items():
            if key.endswith(c) or c.endswith(key):
                rows = v
                break
        mf = {}
        for cells in (rows or []):
            try:
                d = cells[idx.get('EndDate', 5)]
                if not d or d < D0 or d > D1:
                    continue
                vals = []
                for name, _ in FIELDS:
                    j = idx.get(name)
                    vals.append(float(cells[j]) if j is not None and cells[j] not in ('', '--', 'None') else 0.0)
                mf[d] = vals
            except Exception:
                continue
        if mf:
            fw.write(json.dumps({'code': c, 'ok': True, 'mf': mf}, ensure_ascii=False) + '\n')
            okc += 1
        else:
            fw.write(json.dumps({'code': c, 'ok': False}, ensure_ascii=False) + '\n')
    fw.flush()
    print('批 %d/%d（%s...）成功累计 %d，用时 %.0fs' % (nb, (len(todo) + 49) // 50, ch[0], okc, time.time() - t0), flush=True)
fw.close()
print('结束：成功 %d，用时 %.1f 分钟' % (okc, (time.time() - t0) / 60), flush=True)
