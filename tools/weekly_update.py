"""每週自動更新（Gemini 免費額度 + Google 搜尋）。

流程：
 1. 依 tools/weekly_topics.json 的每個主題，請 Gemini 搜尋最近 N 天的新事件。
 2. 追蹤既有「起訴／審理中／有罪未確定／調查中」案件的最新進度（每週輪流檢查一批）。
 3. 程式逐筆檢查 Gemini 的回答（欄位、列舉值、日期、字數），並實際打開每個來源網址，
    確認網址讀得到、頁面有提到對象，才採用。查不到來源的一律丟棄，並寫進 weekly_log.json。
 4. 採用的事件寫進 site/data/inbox.json（stage "ai"、origin "weekly"），APP 會合併顯示；
    來源不夠強（沒有官方文件、也不到兩家媒體）的標示為「待查證」。
    不會修改 entries.json，所以原始資料不會被 AI 改壞。
指令：本檔案讀取 tools/weekly_prompt.md 作為完整指令，可直接在 GitHub 網頁上編輯。
環境變數：GEMINI_API_KEY（必要）、GEMINI_MODEL、WEEKLY_DAYS（預設 10）、WEEKLY_TRACK（預設 10）、WEEKLY_DRY=1（只印不寫）。
"""
import datetime, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import process_inbox as P  # noqa: E402

TOOLS = os.path.dirname(os.path.abspath(__file__))
TZ = datetime.timezone(datetime.timedelta(hours=8))
TODAY = datetime.datetime.now(TZ).date()
POLITICAL = ('民意代表', '政治人物', '組織/政黨')
MAX_NEW = 40
SLEEP = float(os.environ.get('WEEKLY_SLEEP', '7'))   # 免費額度每分鐘次數有限


class RateLimited(Exception):
    pass


def ask(prompt, key, model):
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(key)}'
    body = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}], 'tools': [{'google_search': {}}],
            'generationConfig': {'temperature': 0.1}}
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'content-type': 'application/json'})
            r = json.loads(urllib.request.urlopen(req, timeout=180).read())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited()
            if e.code >= 500 and attempt < 2:
                time.sleep(10)
                continue
            raise
    cand = (r.get('candidates') or [{}])[0]
    txt = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts', []))
    txt = re.sub(r'^```(?:json)?|```$', '', txt.strip(), flags=re.M).strip()
    m = re.search(r'\{[\s\S]*\}', txt)
    if not m:
        raise ValueError('回答裡沒有 JSON')
    return json.loads(m.group(0))


def probe(url):
    """打開來源網址：回傳 (最終網址, 標題+摘要+內文) 或 None。"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (bilei-weekly)'})
        resp = urllib.request.urlopen(req, timeout=25)
        final = resp.geturl()
        raw = resp.read(1_500_000).decode('utf-8', 'ignore')
    except Exception:
        return None
    title = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S | re.I)
    body = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', raw)
    text = re.sub(r'<[^>]+>', ' ', body)
    return final, ((title.group(1) if title else '') + ' ' + text)[:200000]


def canon_names():
    """所有已知名稱 -> 正式名稱（含別名），用正規化字串比對。"""
    m = {}
    for n, c in P.known_names():
        m.setdefault(P.norm(n), c)
    for it in P.load('inbox.json', {'items': []})['items']:
        m.setdefault(P.norm(it['name']), it['name'])
    return m


def existing_index():
    urls, keys = set(), set()
    for f in ('entries.json', 'ext.json'):
        for e in P.load(f, []):
            for ev in e.get('events', []):
                urls |= {s.get('url') for s in ev.get('sources', []) if s.get('url')}
                keys.add((P.norm(e['name']), ev.get('date'), ev.get('tag'), ev.get('status')))
    for it in P.load('inbox.json', {'items': []})['items']:
        ev = it['event']
        urls |= {s.get('url') for s in ev.get('sources', []) if s.get('url')}
        keys.add((P.norm(it['name']), ev.get('date'), ev.get('tag'), ev.get('status')))
    return urls, keys


def validate(c, canon, forced_name=None):
    """檢查一筆 Gemini 事件。回傳 (item 或 None, 原因)。"""
    if not isinstance(c, dict):
        return None, '格式不是物件'
    name = str(c.get('name') or '').strip()
    if forced_name:
        name = forced_name
    if not (1 <= len(name) <= 40):
        return None, '名稱缺漏或過長'
    name = canon.get(P.norm(name), name)
    cat, tag, st = c.get('category'), c.get('tag'), c.get('status')
    if cat not in P.CATS:
        return None, f'category 不合法：{cat}'
    if tag not in P.TAGS:
        return None, f'tag 不合法：{tag}'
    if st not in P.STATUS:
        return None, f'status 不合法：{st}'
    d = str(c.get('date') or '')
    if not re.fullmatch(r'\d{4}(-\d{2}(-\d{2})?)?', d):
        return None, f'日期格式不合法：{d}'
    try:
        if datetime.date.fromisoformat((d + '-01-01')[:10] if len(d) == 4 else (d + '-01')[:10] if len(d) == 7 else d) > TODAY:
            return None, '日期在未來'
    except ValueError:
        return None, f'日期不存在：{d}'
    text = re.sub(r'\s+', ' ', str(c.get('text') or '')).strip()
    if not (10 <= len(text) <= 400):
        return None, '事件說明長度不合'
    if not isinstance(c.get('sources'), list) or not c['sources']:
        return None, '沒有來源'
    keys = {P.norm(name)} | {P.norm(a) for a in (c.get('aliases') or []) if isinstance(a, str) and len(a) >= 2}
    keys |= {P.norm(a) for a, cn in P.known_names() if cn == name and len(a) >= 2}
    verified, seen = [], set()
    for s in c['sources'][:5]:
        u = str((s or {}).get('url') or '')
        if not u.startswith('https://') or u in seen:
            continue
        seen.add(u)
        got = probe(u)
        if not got:
            continue
        final, page = got
        if urllib.parse.urlparse(final).hostname and 'google' in urllib.parse.urlparse(final).hostname:
            continue        # 還在搜尋轉址頁，不算數
        np = P.norm(page)
        if not any(k and k in np for k in keys):
            continue        # 網頁沒有提到對象
        verified.append({'pub': str(s.get('pub') or urllib.parse.urlparse(final).hostname)[:40],
                         't': str(s.get('t') or '')[:100], 'url': final, 'kind': P.kind_of(final)})
    if not verified:
        return None, '來源網址讀不到或沒提到對象'
    official = any(v['kind'] in ('gov', 'court') for v in verified)
    hosts = {urllib.parse.urlparse(v['url']).hostname for v in verified if v['kind'] == 'news'}
    strong = official or len(hosts) >= 2
    if tag in ('性犯罪', '性騷擾') and (not strong or st in ('report', 'invest')):
        return None, '性犯罪／性騷擾需官方文件或兩家媒體，且不收單純指控'
    if st == 'final' and not official:
        st = 'guilty'
    if st in ('final', 'guilty', 'trial', 'admin', 'civil', 'clear') and not strong:
        pass                    # 保留狀態，但標示待查證
    ev = {'date': d, 'tag': tag, 'status': st, 'text': text, 'sources': verified, 'manual': not strong}
    if isinstance(c.get('case'), str) and c['case'].strip():
        ev['case'] = c['case'].strip()[:40]
    if cat in POLITICAL and c.get('party') in P.PARTIES:
        ev['party'] = c['party']
    aliases = [a for a in (c.get('aliases') or []) if isinstance(a, str) and 2 <= len(a) <= 30][:5]
    related = [a for a in (c.get('related') or []) if isinstance(a, str) and 1 <= len(a) <= 40][:6]
    return {'name': name, 'aliases': aliases, 'category': cat, 'related': related, 'summary': text[:80],
            'verified': strong, 'event': ev}, 'ok'


def track_targets(limit, checked):
    cands = []
    for e in P.load('entries.json', []):
        evs = [v for v in e.get('events', []) if not v.get('retracted')]
        if not evs:
            continue
        last = max(evs, key=lambda v: v.get('date', ''))
        if last.get('status') in ('trial', 'guilty', 'invest', 'warn'):
            cands.append((checked.get(e['name'], ''), e, last))
    cands.sort(key=lambda x: x[0])
    return cands[:limit]


def main():
    key = os.environ.get('GEMINI_API_KEY', '')
    if not key:
        print('skipped: GEMINI_API_KEY not set')
        return
    model = os.environ.get('GEMINI_MODEL') or 'gemini-2.5-flash'
    days = int(os.environ.get('WEEKLY_DAYS', '10'))
    dry = os.environ.get('WEEKLY_DRY') == '1'
    template = open(os.path.join(TOOLS, 'weekly_prompt.md'), encoding='utf-8').read()
    topics = json.load(open(os.path.join(TOOLS, 'weekly_topics.json'), encoding='utf-8'))
    inbox = P.load('inbox.json', {'items': []})
    log = P.load('weekly_log.json', {'runs': [], 'checked': {}})
    canon = canon_names()
    urls, keys = existing_index()
    names = '、'.join(sorted({c for c in canon.values()}))
    since = (TODAY - datetime.timedelta(days=days)).isoformat()
    run = {'at': P.NOW, 'model': model, 'topics': [], 'accepted': [], 'rejected': [], 'tracked': []}
    n_new = 0
    seq = 0

    def prompt_for(task, since_d, context=''):
        return (template.replace('{{TODAY}}', TODAY.isoformat()).replace('{{SINCE}}', since_d)
                .replace('{{TASK}}', task).replace('{{NAMES}}', names).replace('{{CONTEXT}}', context))

    def take(cands, label, forced=None):
        nonlocal n_new, seq
        got = 0
        for c in cands:
            if n_new >= MAX_NEW:
                break
            item, why = validate(c, canon, forced)
            nm = (c.get('name') if isinstance(c, dict) else '') or '?'
            if not item:
                run['rejected'].append({'topic': label, 'name': nm, 'reason': why})
                continue
            ev = item['event']
            srcurls = {s['url'] for s in ev['sources']}
            if srcurls & urls or (P.norm(item['name']), ev['date'], ev['tag'], ev['status']) in keys:
                run['rejected'].append({'topic': label, 'name': item['name'], 'reason': '重複（已有相同來源或事件）'})
                continue
            seq += 1
            item['id'] = f'wk-{TODAY:%Y%m%d}-{seq}'
            item.update({'created': P.NOW, 'stage': 'ai', 'origin': 'weekly'})
            ev['inbox'] = item['id']
            inbox['items'].append(item)
            urls.update(srcurls)
            keys.add((P.norm(item['name']), ev['date'], ev['tag'], ev['status']))
            canon.setdefault(P.norm(item['name']), item['name'])
            run['accepted'].append({'name': item['name'], 'tag': ev['tag'], 'status': ev['status'], 'verified': item['verified']})
            n_new += 1
            got += 1
        return got

    try:
        for t in topics:
            try:
                res = ask(prompt_for(t['task'], since), key, model)
                cands = res.get('events') if isinstance(res, dict) else None
                cands = cands if isinstance(cands, list) else []
            except RateLimited:
                raise
            except Exception as e:
                run['topics'].append({'id': t['id'], 'error': str(e)[:100]})
                print('topic failed', t['id'], str(e)[:100])
                time.sleep(SLEEP)
                continue
            got = take(cands, t['label'])
            run['topics'].append({'id': t['id'], 'found': len(cands), 'accepted': got})
            print(f"{t['id']}: found {len(cands)}, accepted {got}")
            time.sleep(SLEEP)
        for _, e, last in track_targets(int(os.environ.get('WEEKLY_TRACK', '10')), log.get('checked', {})):
            ctx = '## 目前資料庫對此對象的紀錄（最新一筆）\n' + json.dumps({k: last.get(k) for k in ('date', 'case', 'tag', 'status', 'text')}, ensure_ascii=False)
            task = (f'追蹤既有案件的最新進度。對象是「{e["name"]}」，name 欄位必須寫「{e["name"]}」。'
                    f'資料庫目前最新一筆事件日期是 {last.get("date")}，狀態是 {last.get("status")}。'
                    '請只搜尋這個日期之後的新進展：新的起訴、判決（一審、二審、三審、更審、定讞）、處分、撤銷或不起訴。'
                    '新進展要輸出成一個新階段事件；沒有新進展就輸出 {"events": []}。')
            try:
                res = ask(prompt_for(task, last.get('date', since), ctx), key, model)
                cands = res.get('events') if isinstance(res, dict) and isinstance(res.get('events'), list) else []
            except RateLimited:
                raise
            except Exception as ex:
                print('track failed', e['name'], str(ex)[:100])
                time.sleep(SLEEP)
                continue
            log.setdefault('checked', {})[e['name']] = P.NOW
            got = take(cands, '追蹤：' + e['name'], forced=e['name'])
            run['tracked'].append({'name': e['name'], 'accepted': got})
            print(f"track {e['name']}: found {len(cands)}, accepted {got}")
            time.sleep(SLEEP)
    except RateLimited:
        run['note'] = '免費額度用完，下週（或手動再執行）繼續'
        print('rate limited; stopping early')

    run['rejected'] = run['rejected'][:60]
    print(f"accepted {len(run['accepted'])}, rejected {len(run['rejected'])}")
    if dry:
        print(json.dumps(run, ensure_ascii=False, indent=1))
        return
    inbox['updated'] = P.NOW
    P.save('inbox.json', inbox)
    log['runs'] = (log.get('runs', []) + [run])[-12:]
    P.save('weekly_log.json', log)
    meta = P.load('meta.json', {})
    meta.update({'lastAuto': P.NOW, 'note': f"每週自動更新：新增 {len(run['accepted'])} 筆（待查證 {sum(1 for a in run['accepted'] if not a['verified'])} 筆），追蹤 {len(run['tracked'])} 件既有案件"})
    P.save('meta.json', meta)


if __name__ == '__main__':
    main()
