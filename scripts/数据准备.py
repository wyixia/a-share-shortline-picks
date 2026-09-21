# -*- coding: utf-8 -*-
"""数据准备：出名单所需的全部数据，一条命令拉齐。

用法:
    python 数据准备.py 2026-09-21 [--days 45]

做什么:
    ① 全市场快照（名称/流通股本/换手/成交额）→ close_snap_latest.json + _all_codes.txt
      （已存在且 30 天内则跳过；来源=东方财富 clist 接口，一次性列表，非每日主源）
    ② 近 N 个自然日的全市场日 K（含 amount/exchange）
      → daily/<T>/kline.jsonl（T 当日 bar，与生产快照同格式）
      → history_kline_<T>.jsonl（完整区间，供 vol3_20/r3x/vchg 用）
    ③ T 日全市场资金流（腾讯全字段）→ daily/<T>/mf.jsonl

产物目录: PICKS_DATA_DIR，未设则默认本包旁的 picks_data/
依赖: node（运行随包的 cli/index.js）、curl（拉快照）
"""
import json, os, sys, subprocess, time, datetime

DATA_DIR = os.environ.get('PICKS_DATA_DIR') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'picks_data')
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(SKILL_DIR, 'cli', 'index.js')
NODE = os.environ.get('NODE_EXE', 'node')
BATCH = 50
UA = 'User-Agent: Mozilla/5.0'


def prefix(code):
    if code.startswith(('6', '9')):
        return 'sh' + code
    if code.startswith(('0', '3')):
        return 'sz' + code
    if code.startswith(('4', '8', '92')):
        return 'bj' + code
    return None


def run_cli(args, timeout=300):
    r = subprocess.run([NODE, CLI] + args, capture_output=True, timeout=timeout)
    return r.stdout.decode('utf-8', 'ignore')


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
        res.setdefault(code, []).append({k: cells[i] for k, i in idx.items()})
    return res


def match(rows_or_map, c):
    """CLI 返回的 symbol 带前缀，与裸代码容错匹配"""
    if isinstance(rows_or_map, dict):
        for k, v in rows_or_map.items():
            if k.endswith(c) or c.endswith(k):
                return v
    return None


# ---------- ① 全市场快照 ----------
def ensure_snapshot():
    fp = os.path.join(DATA_DIR, 'close_snap_latest.json')
    if os.path.exists(fp):
        age = time.time() - os.path.getmtime(fp)
        if age < 30 * 86400:
            print('[快照] 已存在（%.0f 天前），跳过' % (age / 86400), flush=True)
            return fp
    print('[快照] 拉取全市场快照（东财 clist，约 2-3 分钟）...', flush=True)
    HOSTS = ['push2delay.eastmoney.com', 'push2.eastmoney.com',
             '1.push2.eastmoney.com', '7.push2.eastmoney.com']
    FIELDS = 'f2,f3,f5,f6,f8,f12,f14,f20,f21,f10'
    rows = []
    for pn in range(1, 70):
        d = {}
        for host in HOSTS:
            url = (f'https://{host}/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1'
                   f'&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048'
                   f'&fields={FIELDS}')
            for _ in range(3):
                try:
                    r = subprocess.run(['curl', '-s', '--max-time', '25', '-H', UA,
                                        '-H', 'Referer: https://quote.eastmoney.com/', url],
                                       capture_output=True, text=True, timeout=40)
                    dd = json.loads(r.stdout)
                    if dd.get('data') is not None:
                        d = dd; break
                except Exception:
                    pass
                time.sleep(1.0)
            if d:
                break
        data = ((d.get('data') or {}).get('diff')) or []
        if not data:
            print('  page %d: empty, stop' % pn, flush=True)
            break
        rows += data
        if len(data) < 100:
            break
        time.sleep(0.8)
    snap = {}
    for r in rows:
        c = r.get('f12')
        if not c:
            continue
        def num(k):
            v = r.get(k)
            return None if v in ('-', '', None) else float(v)
        snap[c] = {'name': r.get('f14'), 'price': num('f2'), 'chg': num('f3'),
                   'turn': num('f8'), 'vol': num('f5'), 'amt': num('f6'),
                   'mktcap': num('f20'), 'floatcap': num('f21'), 'volratio': num('f10')}
    json.dump(snap, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    codes = [prefix(c) for c in snap if prefix(c)]
    open(os.path.join(DATA_DIR, '_all_codes.txt'), 'w', encoding='utf-8').write('\n'.join(codes))
    print('[快照] 完成：%d 只（%s）' % (len(snap), fp), flush=True)
    return fp


# ---------- ②③ 日 K + 资金流 ----------
def fetch_day(T, days):
    codes = [l.strip() for l in open(os.path.join(DATA_DIR, '_all_codes.txt'), encoding='utf-8') if l.strip()]
    start = (datetime.datetime.strptime(T, '%Y-%m-%d') - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    os.makedirs(os.path.join(DATA_DIR, 'daily', T), exist_ok=True)

    # ② 日 K（区间）→ history + daily/<T>/kline.jsonl
    hk = os.path.join(DATA_DIR, 'history_kline_%s.jsonl' % T.replace('-', ''))
    dk = os.path.join(DATA_DIR, 'daily', T, 'kline.jsonl')
    todo = codes
    if os.path.exists(hk):
        done = set()
        for line in open(hk, encoding='utf-8'):
            try:
                o = json.loads(line)
                if o.get('ok'):
                    done.add(o['code'])
            except Exception:
                pass
        todo = [c for c in codes if c not in done]
    print('[日K] 全市场 %d，待取 %d（%s ~ %s）' % (len(codes), len(todo), start, T), flush=True)
    fw = open(hk, 'a', encoding='utf-8')
    t0 = time.time()
    for i in range(0, len(todo), BATCH):
        ch = todo[i:i + BATCH]
        try:
            got = parse_md(run_cli(['kline', ','.join(ch), '--period', 'day',
                                    '--start', start, '--end', T]))
        except Exception as e:
            print('  批异常', ch[0], type(e).__name__, flush=True); got = {}
        for c in ch:
            allrows = match(got, c) or []
            trow = [r for r in allrows if r.get('date') == T]
            if allrows:
                fw.write(json.dumps({'code': c, 'ok': True, 'ev': allrows}, ensure_ascii=False) + '\n')
            else:
                fw.write(json.dumps({'code': c, 'ok': False}, ensure_ascii=False) + '\n')
            if trow:
                with open(dk, 'a', encoding='utf-8') as f2:
                    f2.write(json.dumps({'code': c, 'ok': True, 'ev': trow}, ensure_ascii=False) + '\n')
        fw.flush()
        if (i // BATCH) % 20 == 0:
            print('  日K %d/%d  用时%.0fs' % (min(i + BATCH, len(todo)), len(todo), time.time() - t0), flush=True)
    fw.close()
    print('[日K] 完成，用时 %.0fs' % (time.time() - t0), flush=True)

    # ③ 资金流（T 当日，全字段）
    mf = os.path.join(DATA_DIR, 'daily', T, 'mf.jsonl')
    todo = codes
    if os.path.exists(mf):
        done = set()
        for line in open(mf, encoding='utf-8'):
            try:
                o = json.loads(line)
                if o.get('ok'):
                    done.add(o['code'])
            except Exception:
                pass
        todo = [c for c in codes if c not in done]
    print('[资金流] 全市场 %d，待取 %d（%s）' % (len(codes), len(todo), T), flush=True)
    fm = open(mf, 'a', encoding='utf-8')
    t0 = time.time()
    for i in range(0, len(todo), BATCH):
        ch = todo[i:i + BATCH]
        try:
            got = parse_md(run_cli(['fund', 'flow', ','.join(ch), '--start', T, '--end', T]))
        except Exception as e:
            print('  批异常', ch[0], type(e).__name__, flush=True); got = {}
        for c in ch:
            rws = match(got, c) or []
            trow = [r for r in rws if r.get('date') == T or r.get('EndDate') == T]
            if trow:
                fm.write(json.dumps({'code': c, 'ok': True, 'ev': trow}, ensure_ascii=False) + '\n')
            else:
                fm.write(json.dumps({'code': c, 'ok': False}, ensure_ascii=False) + '\n')
        fm.flush()
        if (i // BATCH) % 20 == 0:
            print('  资金流 %d/%d  用时%.0fs' % (min(i + BATCH, len(todo)), len(todo), time.time() - t0), flush=True)
    fm.close()
    print('[资金流] 完成，用时 %.0fs' % (time.time() - t0), flush=True)


if __name__ == '__main__':
    T = None; days = 45
    args = sys.argv[1:]
    if '--days' in args:
        i = args.index('--days'); days = int(args[i + 1]); args = args[:i] + args[i + 2:]
    T = args[0] if args else datetime.date.today().strftime('%Y-%m-%d')
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, 'daily', T), exist_ok=True)
    print('数据目录:', DATA_DIR, '| 目标日:', T, '| 历史深度:', days, '自然日', flush=True)
    ensure_snapshot()
    fetch_day(T, days)
    print('[完成] 现在可运行: python scripts/出名单.py %s' % T, flush=True)
