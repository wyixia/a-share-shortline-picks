# -*- coding: utf-8 -*-
"""K 线历史回补（低并发 + 多重重试 + 断点续跑）

用法:
    python 补数_历史K线.py [BEG] [END]     # 缺省 BEG=20150101，END=今天
示例:
    python 补数_历史K线.py 20240101 20260921

输出 backfill_kline.jsonl：{"code":..,"ok":..,"ev":[[date,open,close,high,low,vol,amt,turn],...]}
vol 单位=手；amt=元；turn=换手率%。数据目录 = 环境变量 PICKS_DATA_DIR，未设则默认本仓库旁 picks_data/。
"""
import os, json, time, threading, datetime, urllib.request, ssl, random
from concurrent.futures import ThreadPoolExecutor

ssl._create_default_https_context = ssl._create_unverified_context
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get('PICKS_DATA_DIR') or os.path.join(ROOT, 'picks_data')
os.makedirs(OUT, exist_ok=True)
SKILL_W = os.path.join(ROOT, 'weights')
BK = os.path.join(OUT, 'backfill_kline.jsonl')
import glob as _glob
_snaps = sorted(_glob.glob(os.path.join(OUT, 'close_snap_*.json')))
if not _snaps:
    raise SystemExit('[!] 数据目录缺少 close_snap_*.json。请先运行: python scripts/准备数据.py <交易日>')
SNAP = _snaps[-1]
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
     'Referer': 'https://quote.eastmoney.com/'}
argv = [a for a in __import__('sys').argv[1:]]
END = (argv[1] if len(argv) > 1 else datetime.date.today().strftime('%Y%m%d')).replace('-', '')
BEG = (argv[0] if len(argv) > 0 else '20150101').replace('-', '')
print(f'回补窗口 {BEG} ~ {END} | 数据目录 {OUT}', flush=True)
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
