"""私人資料投遞箱：不需要密語就能新增私人條目。

  python3 tools/private_queue.py add items.json   用公鑰加密，存成 private_queue/<id>.enc（任何人都能投遞，但讀不出內容）
  python3 tools/private_queue.py merge            需要 BILEI_PASSPHRASE：解開私鑰與投遞件，併入 site/data/private.enc 後刪除投遞件

公鑰：tools/private_queue_pub.txt；私鑰以密語加密存在 tools/private_queue_key.enc。
GitHub Actions（inbox.yml）有 BILEI_PASSPHRASE secret 時會自動 merge。
"""
import base64, glob, hashlib, json, os, sys, time
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q = os.path.join(ROOT, 'private_queue')
PUB = os.path.join(ROOT, 'tools', 'private_queue_pub.txt')
KEY = os.path.join(ROOT, 'tools', 'private_queue_key.enc')
PRIV = os.path.join(ROOT, 'site', 'data', 'private.enc')
b64e = lambda x: base64.b64encode(x).decode()
b64d = base64.b64decode
RAW = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def pw_encrypt(pw, data):
    salt, iv, it = os.urandom(16), os.urandom(12), 310000
    k = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, it, 32)
    return {"v": 1, "iter": it, "salt": b64e(salt), "iv": b64e(iv), "ct": b64e(AESGCM(k).encrypt(iv, data, None))}


def pw_decrypt(pw, j):
    k = hashlib.pbkdf2_hmac('sha256', pw.encode(), b64d(j['salt']), j['iter'], 32)
    return AESGCM(k).decrypt(b64d(j['iv']), b64d(j['ct']), None)


def hk(shared):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b'bilei-queue').derive(shared)


def add(path):
    pub = X25519PublicKey.from_public_bytes(b64d(open(PUB).read().strip()))
    eph = X25519PrivateKey.generate(); iv = os.urandom(12)
    ct = AESGCM(hk(eph.exchange(pub))).encrypt(iv, open(path, 'rb').read(), None)
    os.makedirs(Q, exist_ok=True)
    out = os.path.join(Q, f"{int(time.time())}-{os.urandom(3).hex()}.enc")
    json.dump({"epk": b64e(eph.public_key().public_bytes(**RAW)), "iv": b64e(iv), "ct": b64e(ct)}, open(out, 'w'))
    print('queued', out)


def merge():
    pw = os.environ.get('BILEI_PASSPHRASE', '')
    files = sorted(glob.glob(os.path.join(Q, '*.enc')))
    if not files: print('queue empty'); return
    if not pw: print('merge skipped: BILEI_PASSPHRASE not set'); return
    sk = X25519PrivateKey.from_private_bytes(pw_decrypt(pw, json.load(open(KEY))))
    cur = json.loads(pw_decrypt(pw, json.load(open(PRIV)))) if os.path.exists(PRIV) else []
    idx = {e['id']: i for i, e in enumerate(cur)}
    for f in files:
        j = json.load(open(f))
        k = hk(sk.exchange(X25519PublicKey.from_public_bytes(b64d(j['epk']))))
        for e in json.loads(AESGCM(k).decrypt(b64d(j['iv']), b64d(j['ct']), None)):
            if e['id'] in idx: cur[idx[e['id']]] = e
            else: idx[e['id']] = len(cur); cur.append(e)
    json.dump(pw_encrypt(pw, json.dumps(cur, ensure_ascii=False).encode()), open(PRIV, 'w'))
    for f in files: os.remove(f)
    print('merged', len(files), 'files; private entries', len(cur))


def keygen():
    pw = os.environ['BILEI_PASSPHRASE']; sk = X25519PrivateKey.generate()
    raw = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    json.dump(pw_encrypt(pw, raw), open(KEY, 'w'))
    open(PUB, 'w').write(b64e(sk.public_key().public_bytes(**RAW)) + '\n')
    print('keypair written')


if __name__ == '__main__':
    {'add': lambda: add(sys.argv[2]), 'merge': merge, 'keygen': keygen}[sys.argv[1]]()
