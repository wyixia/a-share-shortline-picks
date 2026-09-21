# -*- coding: utf-8 -*-
"""一键准备数据：给定交易日 T，从公开接口拉取出名单所需的全部数据。

用法:
    python 准备数据.py 2026-09-18 [--force]

数据目录:
    环境变量 PICKS_DATA_DIR；未设置时默认使用本仓库旁的 picks_data/（自动创建）

产物:
    daily/<T>/kline.jsonl          T 日全市场K线（含换手率/成交额，出名单的当日因子）
    close_snap_<T>.json            名称/流通市值/最新价快照（算流通股本用）
    backfill_kline_<T>.jsonl       近端历史K线（约30个交易日，出名单算量比/涨幅因子用）
    daily/<T>/mf.jsonl             T 日腾讯口径主力净流入（仅 T=今日时拉取；失败可缺，名单照出）

说明:
    - 首次跑约 10~40 分钟（每只股票 1 次K线请求）；同日重跑只补缺，很快。
    - 份额/换手等因子直接取接口直给值；资金流为腾讯口径，与权重训练口径一致。
    - T 为历史交易日时不拉资金流（腾讯公开接口仅提供当日），名单中"主力净流入"列留空。
"""
import json, os, sys, time, ssl, datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ssl._create_default_https_context = ssl._create_unverified_context

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('PICKS_DATA_DIR') or os.path.join(ROOT, 'picks_data')
os.makedirs(DATA_DIR, exist_ok=True)
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
     'Referer': 'https://quote.eastmoney.com/'}
WORKERS = 4
HIST_NATURAL_DAYS = 60   # 自然日窗口，覆盖约 30+ 个交易日


def fetch(url, retry=4, timeout=30):
    last = None
    for i in range(retry):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=timeout).read()
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError('%s -> %s' % (url, last))


def secid(code):
    return ('1.' if code[0] == '6' else '0.') + code


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    force = '--force' in sys.argv
    if not args:
        raise SystemExit('用法: python 准备数据.py 2026-09-18 [--force]')
    T = args[0].replace('/', '-')

    # ---------- Step 1: 全市场代码 + 名称 + 流通市值（clist 分页，约 52 页） ----------
    print('[1/3] 拉取全市场代码/名称/流通市值 ...', flush=True)
    uni = {}
    pn = 1
    while True:
        url_t = ('https://%s/api/qt/clist/get?pn=%d&pz=100&po=1&np=1&fltt=2&invt=2&fid=f12'
                 '&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&fields=f12,f14,f2,f21')
        diff = None
        dead_hosts = set()
        for host in ('push2.eastmoney.com', 'push2delay.eastmoney.com'):
            if host in dead_hosts:
                continue
            try:
                o = json.loads(fetch(url_t % (host, pn), retry=3))
                diff = (o.get('data') or {}).get('diff') or []
                break
            except Exception:
                dead_hosts.add(host)   # 本轮列表拉取中不再重试该主机
                continue
        if diff is None:
            if pn == 1:
                print('      东财列表不可用，改用新浪 hs_a 全市场列表 ...', flush=True)
                page = 1
                while page <= 80:
                    u = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                         'Market_Center.getHQNodeData?page=%d&num=100&sort=symbol&asc=1&node=hs_a' % page)
                    try:
                        arr = json.loads(fetch(u, retry=3, timeout=20).decode('gbk', 'ignore'))
                    except Exception:
                        break
                    if not arr:
                        break
                    for d in arr:
                        c = d.get('code') or ''
                        if len(c) != 6:
                            continue
                        try:
                            uni[c] = {'name': d.get('name') or '',
                                      'price': float(d.get('trade') or 0),
                                      'floatcap': float(d.get('nmc') or 0) * 1e4}  # nmc 万元 -> 元
                        except (TypeError, ValueError):
                            continue
                    page += 1
                    time.sleep(0.4)
            else:
                print('      第 %d 页失败（已重试），用已有数据继续' % pn, flush=True)
            break
        if not diff:
            break
        for d in diff:
            c, name = d.get('f12'), d.get('f14') or ''
            px, fc = d.get('f2'), d.get('f21')
            if not c or px in ('-', None) or fc in ('-', None):
                continue
            try:
                uni[c] = {'name': name, 'price': float(px), 'floatcap': float(fc)}
            except (TypeError, ValueError):
                continue
        pn += 1
        time.sleep(0.4)
        if pn > 120:
            break
    print('      全市场 %d 只' % len(uni), flush=True)
    if not uni:
        raise SystemExit('[!] 全市场代码列表拉取失败，请检查网络后重试')

    snap_path = os.path.join(DATA_DIR, 'close_snap_%s.json' % T.replace('-', ''))
    json.dump(uni, open(snap_path, 'w', encoding='utf-8'), ensure_ascii=False)
    print('      输出 %s' % snap_path, flush=True)

    # ---------- Step 2: 每只股票K线（近 60 自然日），得到 T 日 bar + 历史因子窗口 ----------
    daily_dir = os.path.join(DATA_DIR, 'daily', T)
    os.makedirs(daily_dir, exist_ok=True)
    kp = os.path.join(daily_dir, 'kline.jsonl')
    bp = os.path.join(DATA_DIR, 'backfill_kline_%s.jsonl' % T.replace('-', ''))
    beg = (datetime.datetime.strptime(T, '%Y-%m-%d') - datetime.timedelta(days=HIST_NATURAL_DAYS)).strftime('%Y%m%d')
    end = T.replace('-', '')

    done = set()
    if os.path.exists(kp) and not force:
        for line in open(kp, encoding='utf-8'):
            try:
                done.add(json.loads(line)['code'])
            except Exception:
                continue
        if done:
            print('[2/3] 已有 %d 只的 T 日K线，续跑只补缺 (--force 重拉)' % len(done), flush=True)

    todo = [c for c in uni if c not in done]
    url_tpl = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s&klt=101&fqt=0'
               '&beg=%s&end=%s&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f61'
               '&ut=fa5fd1943c7b386f172d6893dbfba10b')

    lock_write = __import__('threading').Lock()
    fk = open(kp, 'a', encoding='utf-8')
    fb = open(bp, 'a', encoding='utf-8')
    n_ok, n_no_bar, n_fail = 0, 0, 0
    t0 = time.time()

    def job(c):
        try:
            time.sleep(0.1)
            o = json.loads(fetch(url_tpl % (secid(c), beg, end)))
            ks = (o.get('data') or {}).get('klines') or []
        except Exception:
            return ('fail', c, None, None)
        tbar, hist = None, []
        for k in ks:
            p = k.split(',')
            # f51日期, f52开, f53收, f54高, f55低, f56量(手), f57额(元), f61换手%
            try:
                rec = [p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])]
            except (ValueError, IndexError):
                continue
            hist.append(rec)
            if p[0] == T:
                try:
                    turn = float(p[7])
                except (ValueError, IndexError):
                    turn = None
                tbar = {'date': p[0], 'open': float(p[1]), 'last': float(p[2]), 'high': float(p[3]),
                        'low': float(p[4]), 'volume': float(p[5]), 'amount': float(p[6]), 'exchange': turn}
        if not ks:
            return ('fail', c, None, None)
        if tbar is None:
            return ('nobar', c, None, None)
        return ('ok', c, tbar, hist)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(job, c): c for c in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            st, c, tbar, hist = fu.result()
            with lock_write:
                if st == 'ok':
                    fk.write(json.dumps({'code': c, 'ok': True, 'ev': [tbar]}, ensure_ascii=False) + '\n')
                    fb.write(json.dumps({'code': c, 'ok': True, 'ev': hist}, ensure_ascii=False) + '\n')
                    n_ok += 1
                elif st == 'nobar':
                    n_no_bar += 1
                else:
                    n_fail += 1
            if i % 500 == 0:
                fk.flush(); fb.flush()
                print('      %d/%d  用时%.0fs' % (i, len(todo), time.time() - t0), flush=True)
    fk.close(); fb.close()
    print('      完成: T日bar %d 只 | 停牌/无bar %d | 失败 %d' % (n_ok, n_no_bar, n_fail), flush=True)
    print('      输出 %s' % kp, flush=True)
    print('      输出 %s' % bp, flush=True)
    if n_ok == 0:
        raise SystemExit('[!] 没有任何股票取到 %s 的K线：日期可能有误或非交易日' % T)

    # ---------- Step 3: 资金流（腾讯口径；仅 T=今日可拉） ----------
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    mp = os.path.join(daily_dir, 'mf.jsonl')
    if T == today:
        print('[3/3] 拉取腾讯口径主力净流入（当日）...', flush=True)
        codes = sorted(uni)
        got = 0
        with open(mp, 'w', encoding='utf-8') as fm:
            for i in range(0, len(codes), 50):
                batch = codes[i:i + 50]
                q = ','.join(('ff_sh' if c[0] == '6' else 'ff_sz') + c for c in batch)
                try:
                    txt = fetch('https://qt.gtimg.cn/q=' + q, retry=2, timeout=15).decode('gbk', errors='replace')
                except Exception:
                    continue
                for seg in txt.split(';'):
                    seg = seg.strip()
                    if not seg or '=' not in seg:
                        continue
                    head, body = seg.split('=', 1)
                    parts = body.strip('"').split('~')
                    # v_ff_shXXXXXX="m~名称~代码~主力流入(万)~主力流出(万)~主力净流入(万)~..."
                    if len(parts) > 5 and parts[2]:
                        c = parts[2].zfill(6)
                        try:
                            main_net = float(parts[5]) * 1e4  # 万 -> 元
                        except ValueError:
                            continue
                        fm.write(json.dumps({'code': c, 'ok': True,
                                             'ev': [{'date': T, 'MainNetFlow': main_net}]}) + '\n')
                        got += 1
                time.sleep(0.3)
        print('      资金流 %d 只 | 输出 %s' % (got, mp), flush=True)
    else:
        print('[3/3] 跳过资金流（%s 非今日，腾讯公开接口仅提供当日主力净流入）。' % T, flush=True)
        print('      名单照出，"主力净流入"列留空、该因子按中性处理。', flush=True)

    print('\n数据就绪。下一步: python scripts/出名单.py %s' % T, flush=True)


if __name__ == '__main__':
    main()
