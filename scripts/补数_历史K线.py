# -*- coding: utf-8 -*-
"""K 线历史回补 v2（低并发 + 多重重试 + 断点续跑）
窗口 2021-09-01 ~ 2024-12-31（含 2024 全年，用于与现有 westock 数据做口径校验）
输出 backfill_kline.jsonl：{"code":..,"ok":..,"ev":[[date,open,close,high,low,vol,amt,turn],...]}
vol 单位=手；amt=元；turn=换手率%
"""
import os, json, os, time, threading, urllib.request, ssl, random
from concurrent.futures import ThreadPoolExecutor

ssl._create_default_https_context = ssl._create_unverified_context
DATA_DIR = os.environ.get('PICKS_DATA_DIR')
if not DATA_DIR:
    raise SystemExit('[!] 未设置数据目录。请先设置环境变量 PICKS_DATA_DIR 指向你的数据目录\n'                     '    （需含 daily/<交易日>/kline.jsonl、历史日K jsonl、close_snap_*.json，详见 README）。\n'                     '    数据可从零回补：先跑 scripts/补数_历史K线.py 与 scripts/补数_资金流.py。')
SKILL_W = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'weights')
BK = os.path.join(OUT, 'backfill_kline.jsonl')
SNAP = os.path.join(OUT, 'close_snap_20260910.json')
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
     'Referer': 'https://quote.eastmoney.com/'}
BEG, END = '20210901', '20241231'
WORKERS = 2
RETRY = 6

snap = json.load(open(SNAP, encoding='utf-8'))
codes = sorted([c for c, v in snap.items() if v.get('floatcap') and v.get('price')])
okset = set()
if os.path.exists(BK):
    for line in open(BK, encoding='utf-8'):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get('ok') and o.get('ev'):
            okset.add(o['code'])
todo = [c for c in codes if c not in okset]
print(f'总 {len(codes)}，已有 {len(okset)}，待取 {len(todo)}', flush=True)


def secid(c):
    return ('1.' + c) if c.startswith('6') else ('0.' + c)


def one(c):
    url = (f'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid(c)}'
           f'&klt=101&fqt=1&beg={BEG}&end={END}'
           f'&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61')
    delay = 1.0
    for a in range(RETRY):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=25)\
                .read().decode('utf-8', 'ignore')
            d = json.loads(raw)
            kl = ((d.get('data') or {}).get('klines')) or []
            ev = []
            for s in kl:
                p = s.split(',')
                if len(p) < 11:
                    continue
                try:
                    ev.append([p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4]),
                               float(p[5]), float(p[6]), float(p[10])])
                except Exception:
                    continue
            if ev:
                return {'code': c, 'ok': True, 'src': 'em', 'ev': ev}
        except Exception:
            pass
        time.sleep(delay + random.random() * 0.4)
        delay = min(delay * 1.8, 8.0)
    return {'code': c, 'ok': False, 'ev': []}


lock = threading.Lock()
fh = open(BK, 'a', encoding='utf-8')
cnt = {'ok': 0, 'fail': 0}


def worker(c):
    o = one(c)
    with lock:
        fh.write(json.dumps(o, ensure_ascii=False) + '\n')
        fh.flush()
        cnt['ok' if o['ok'] else 'fail'] += 1
        t = cnt['ok'] + cnt['fail']
        if t % 300 == 0:
            print(f'  {t}/{len(todo)} ok={cnt["ok"]} fail={cnt["fail"]}', flush=True)


with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    list(ex.map(worker, todo))
fh.close()
print(f'完成 ok={cnt["ok"]} fail={cnt["fail"]}', flush=True)
