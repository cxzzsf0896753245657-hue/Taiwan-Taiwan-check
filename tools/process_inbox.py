"""處理 APP 上傳的收件匣（inbox/）。

inbox/<id>.json  公開的初步草稿（APP 用關鍵字規則分類；不含截圖）
inbox/<id>.enc   加密的原始資料（截圖、全文、連結），只有 BILEI_PASSPHRASE 解得開

第一階段（免費、免金鑰）：讀取草稿與連結頁面的標題／摘要，用 site/data/rules.json 分類，
  寫進 site/data/inbox.json，標示「待查證」。所有裝置重新整理後就看得到。
第二階段（有 GEMINI_API_KEY 與 BILEI_PASSPHRASE 時）：解密原始資料，交給 Gemini 免費額度
  看圖、讀連結並用 Google 搜尋查證，覆蓋初步判斷；修改前的版本保留在 site/data/inbox_history.json。
  完成後刪除 inbox/ 裡的原始檔（截圖不會留在網站上）。
"""
import base64, datetime, glob, html, json, os, re, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'site', 'data')
INBOX = os.path.join(ROOT, 'inbox')
RULES = json.load(open(os.path.join(DATA, 'rules.json'), encoding='utf-8'))
NOW = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
CATS = ['食品/產品', '企業/品牌', 'App/科技', '企業人物', '民意代表', '政治人物', '藝人/公眾人物', '組織/政黨', '其他']
TAGS = ['食安', '涉中', '貪污', '犯罪/司法', '性犯罪', '性騷擾', '性別歧視', '資安', '詐騙', '其他']
STATUS = ['final', 'guilty', 'trial', 'admin', 'civil', 'warn', 'invest', 'report', 'affected', 'clear']
PARTIES = ['國民黨', '民進黨', '民眾黨', '時代力量', '無黨籍', '其他政黨']


def load(name, default):
    p = os.path.join(DATA, name)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default


def save(name, obj):
    with open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def norm(s):
    return re.sub(r'[\s・·．.\-（）()「」『』]', '', str(s or '')).lower()


def known_names():
    out = []
    for f in ('entries.json', 'ext.json'):
        for e in load(f, []):
            out.append((e['name'], e['name']))
            out += [(a, e['name']) for a in e.get('aliases', []) if len(a) >= 2]
    return sorted(out, key=lambda x: -len(x[0]))


def first(pairs, text, default):
    for val, pat in pairs:
        if re.search(pat, text):
            return val
    return default


def classify(text, name=''):
    """C 方案：純關鍵字規則。"""
    t = text or ''
    if not name:
        for alias, canon in known_names():
            if alias in t:
                name = canon
                break
    if not name:
        for pat in RULES['name_patterns']:
            m = re.search(pat, t)
            if m:
                name = m.group(1)
                break
    party = next((p for p in RULES['parties'] if p in t), None)
    d = re.search(r'(20\d\d)[年/\-.](\d{1,2})[月/\-.](\d{1,2})', t)
    roc = re.search(r'(1[01]\d)年(\d{1,2})月(\d{1,2})日', t)
    date = (f'{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}' if d else
            f'{int(roc.group(1)) + 1911}-{int(roc.group(2)):02d}-{int(roc.group(3)):02d}' if roc else NOW[:10])
    return {'name': name, 'category': first(RULES['categories'], t, '其他'), 'tag': first(RULES['tags'], t, '其他'),
            'status': first(RULES['status'], t, 'report'), 'party': party, 'date': date}


def fetch_page(url):
    """讀連結頁面的標題、媒體名稱與摘要（不需要金鑰）。"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (bilei-inbox)'})
        raw = urllib.request.urlopen(req, timeout=20).read(1_500_000).decode('utf-8', 'ignore')
    except Exception as e:
        return {'url': url, 'error': str(e)[:80]}
    def meta(k):
        m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']+)' % re.escape(k), raw, re.I) or \
            re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(k), raw, re.I)
        return html.unescape(m.group(1)).strip() if m else ''
    title = meta('og:title') or html.unescape((re.search(r'<title[^>]*>(.*?)</title>', raw, re.S | re.I) or [None, ''])[1]).strip()
    body = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', raw)
    paras = [html.unescape(re.sub(r'<[^>]+>', '', p)).strip() for p in re.findall(r'<p[^>]*>([\s\S]*?)</p>', body)]
    text = ' '.join(p for p in paras if len(p) > 15)[:6000]
    return {'url': url, 'title': title[:120], 'site': meta('og:site_name') or urllib.parse.urlparse(url).hostname,
            'desc': meta('og:description') or meta('description'), 'published': meta('article:published_time'), 'text': text}


def kind_of(url):
    return 'court' if 'judicial.gov.tw' in url else 'gov' if re.search(r'\.gov\.tw', url) else 'news'


def stage1(items, done_ids):
    """把新的草稿加進 inbox.json（免費、免金鑰）。"""
    added = 0
    for path in sorted(glob.glob(os.path.join(INBOX, '*.json'))):
        d = json.load(open(path, encoding='utf-8'))
        if d['id'] in done_ids:
            continue
        pages = [fetch_page(u) for u in d.get('urls', [])[:5]]
        text = '\n'.join([d.get('text', '')] + [' '.join([p.get('title', ''), p.get('desc', '')]) for p in pages])
        c = classify(text, d.get('name', ''))
        draft = d.get('draft') or {}
        for k in ('category', 'tag', 'status', 'party', 'date'):   # APP 已經判斷過的優先，除非是預設值
            if draft.get(k) and draft[k] not in ('其他', 'report') and k != 'date':
                c[k] = draft[k]
        name = c['name'] or draft.get('name') or ('待整理：' + (pages[0].get('title', '')[:14] if pages else d.get('text', '')[:14]).strip())
        summary = (pages[0].get('desc') if pages and pages[0].get('desc') else d.get('text', ''))[:200]
        sources = [{'pub': p.get('site') or '連結', 't': p.get('title') or '使用者提供連結', 'url': p['url'], 'kind': kind_of(p['url'])}
                   for p in pages] or [{'pub': '使用者上傳', 't': '截圖／文字，待查證', 'kind': 'news'}]
        items.append({'id': d['id'], 'created': d.get('created', NOW), 'stage': 'draft', 'name': name, 'aliases': [],
                      'category': c['category'] if c['category'] in CATS else '其他', 'related': [], 'summary': summary,
                      'event': {'date': c['date'], 'tag': c['tag'], 'status': c['status'], 'text': summary or '（待整理）',
                                **({'party': c['party']} if c['party'] and c['category'] in ('民意代表', '政治人物') else {}),
                                'manual': True, 'inbox': d['id'], 'sources': sources}})
        done_ids.add(d['id'])
        added += 1
    return added


PROMPT = """你是台灣事實查核整理員。使用者上傳了一則可能需要「避雷」的資料（截圖、文字或連結）。
請先看懂內容，再用 Google 搜尋查證：找出原始新聞、政府公告或判決，確認事件是否屬實、目前審級或處分狀態。
只能寫有來源支持的事實，不要推測。
只輸出一個 JSON 物件，不要任何其他文字：
{"ok": true/false（是否為可收錄的具體事件：食安、涉中、貪污、犯罪司法、性犯罪、性騷擾、性別歧視、資安、詐騙等）,
 "verified": true/false（是否找到可靠來源證實）,
 "name": "事件主體名稱（人名、公司或產品；已有名稱清單中的同一對象請用完全相同名稱）",
 "aliases": [], "category": "%s 其中之一", "tag": "%s 其中之一", "status": "%s 其中之一（final 判決確定、guilty 有罪未確定、trial 起訴審理中、admin 行政處分、civil 民事、warn 官方警示、invest 調查中、report 媒體報導、affected 受影響、clear 無罪或不起訴）",
 "party": "政治人物事件當時所屬政黨（%s；不確定就留空）", "date": "YYYY-MM-DD 事件日期",
 "summary": "一句話摘要（60 字內）", "text": "事件經過與目前狀態（150 字內，繁體中文）",
 "related": ["相關公司、產品、人名"],
 "sources": [{"pub": "媒體或機關名稱", "t": "標題", "url": "原始網址"}]}
""" % ('、'.join(CATS), '、'.join(TAGS), '、'.join(STATUS), '、'.join(PARTIES))


def decrypt(path, passphrase):
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    j = json.load(open(path))
    b = lambda x: base64.b64decode(x)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b(j['salt']), iterations=j['iter']).derive(passphrase.encode())
    return json.loads(AESGCM(key).decrypt(b(j['iv']), b(j['ct']), None))


def gemini(parts, key, model):
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(key)}'
    body = {'contents': [{'role': 'user', 'parts': parts}], 'tools': [{'google_search': {}}],
            'generationConfig': {'temperature': 0.2}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'content-type': 'application/json'})
    r = json.loads(urllib.request.urlopen(req, timeout=120).read())
    cand = (r.get('candidates') or [{}])[0]
    txt = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts', []))
    m = re.search(r'\{[\s\S]*\}', txt)
    if not m:
        raise ValueError('AI 沒有回傳 JSON')
    v = json.loads(m.group(0))
    chunks = [(c.get('web') or {}) for c in (cand.get('groundingMetadata') or {}).get('groundingChunks', [])]
    return v, [{'pub': c.get('title', '搜尋來源'), 't': '查證來源', 'url': c['uri'], 'kind': 'news'} for c in chunks if c.get('uri')]


def stage2(items, history):
    key, pw = os.environ.get('GEMINI_API_KEY', ''), os.environ.get('BILEI_PASSPHRASE', '')
    model = os.environ.get('GEMINI_MODEL') or 'gemini-2.5-flash'
    if not key or not pw:
        print('stage2 skipped: GEMINI_API_KEY / BILEI_PASSPHRASE not set')
        return 0
    names = '、'.join(n for n, _ in known_names()[:600])
    n = 0
    for it in items:
        if it['stage'] != 'draft':
            continue
        enc = os.path.join(INBOX, it['id'] + '.enc')
        if not os.path.exists(enc):
            continue
        try:
            raw = decrypt(enc, pw)
        except Exception as e:
            print('decrypt failed', it['id'], e)
            continue
        pages = [fetch_page(u) for u in raw.get('urls', [])[:5]]
        parts = [{'text': PROMPT + '\n已有名稱清單：' + names}]
        parts.append({'text': '使用者文字：\n' + (raw.get('text') or '（無）')[:8000]})
        for p in pages:
            parts.append({'text': f"連結 {p['url']}\n標題：{p.get('title','')}\n摘要：{p.get('desc','')}\n內文：{p.get('text','')[:4000]}"})
        for img in raw.get('images', [])[:4]:
            parts.append({'inline_data': {'mime_type': 'image/jpeg', 'data': img}})
        try:
            v, grounding = gemini(parts, key, model)
        except Exception as e:
            print('gemini failed', it['id'], str(e)[:200])
            if '429' in str(e):
                break          # 免費額度用完，下次排程再處理
            continue
        history.append({'id': f"h-{it['id']}-{len(history)}", 'inboxId': it['id'], 'at': NOW, 'by': 'auto',
                        'reason': 'AI 查證整理（取代關鍵字初判）', 'before': json.loads(json.dumps(it))})
        if not v.get('ok'):
            it['stage'] = 'rejected'
            it['note'] = 'AI 判斷不是可收錄的具體事件'
        else:
            src = [{'pub': str(s.get('pub', '來源'))[:40], 't': str(s.get('t', ''))[:80], 'url': s['url'], 'kind': kind_of(s['url'])}
                   for s in v.get('sources', []) if isinstance(s, dict) and str(s.get('url', '')).startswith('https://')]
            src = src or grounding[:4] or it['event']['sources']
            it.update({'stage': 'ai', 'name': str(v.get('name') or it['name']), 'aliases': [str(a) for a in v.get('aliases', [])][:6],
                       'category': v.get('category') if v.get('category') in CATS else it['category'],
                       'related': [str(a) for a in v.get('related', [])][:8], 'summary': str(v.get('summary') or it['summary'])[:200],
                       'verified': bool(v.get('verified'))})
            ev = {'date': v.get('date') if re.fullmatch(r'\d{4}(-\d{2}){0,2}', str(v.get('date', ''))) else it['event']['date'],
                  'tag': v.get('tag') if v.get('tag') in TAGS else it['event']['tag'],
                  'status': v.get('status') if v.get('status') in STATUS else it['event']['status'],
                  'text': str(v.get('text') or it['event']['text'])[:600], 'inbox': it['id'], 'sources': src}
            if v.get('party') in PARTIES and it['category'] in ('民意代表', '政治人物'):
                ev['party'] = v['party']
            if not v.get('verified'):
                ev['manual'] = True
            it['event'] = ev
        for ext in ('.enc', '.json'):
            p = os.path.join(INBOX, it['id'] + ext)
            if os.path.exists(p):
                os.remove(p)            # 原始截圖與全文不留在網站
        n += 1
    return n


def main():
    os.makedirs(INBOX, exist_ok=True)
    state = load('inbox.json', {'items': []})
    items = state['items']
    history = load('inbox_history.json', [])
    a = stage1(items, {i['id'] for i in items})
    b = stage2(items, history)
    state.update({'items': items, 'updated': NOW})
    save('inbox.json', state)
    save('inbox_history.json', history)
    print(f'drafts added: {a}, AI processed: {b}, total: {len(items)}')


if __name__ == '__main__':
    main()
