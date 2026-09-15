"""M3 评估+清洗 验证：evaluate / ingest / upload 三条路都返回 evaluation + cleaned_preview"""
import sys, os, requests
sys.path.insert(0, '.')
sys.path = [p for p in sys.path if 'hermes-agent' not in p]
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

BASE = 'http://127.0.0.1:8123'
USER = 'm2tester'

def login():
    r = requests.post(BASE + '/api/login', json={'username': USER, 'password': '123456', 'role': 'personal'}, timeout=60)
    print('login', r.status_code, r.text[:120])
    r.raise_for_status()
    return {'token': r.json()['token']}

def status(H):
    r = requests.get(BASE + '/api/kb/status', params=H, timeout=60)
    print('status', r.status_code, r.text[:300])
    r.raise_for_status()
    return r.json()

def clean_all(H, st):
    for d in st.get('docs', []):
        requests.delete(BASE + '/api/kb/docs/' + d['source_id'], params=H, timeout=60)

def main():
    H = login()
    dirty = '奴婢给主人整理的呢～(≧∇≦)ﾉ\n| A | B |\n| --- | --- |\n| 1 | 2 |\n昇腾 910C 良率爬坡顺利。'

    # A evaluate
    ra = requests.post(BASE + '/api/kb/evaluate', params=H, json={'title': 'dirty', 'content': dirty}, timeout=120)
    print('evaluate', ra.status_code, ra.text[:400])
    ra.raise_for_status()

    # B ingest
    rb = requests.post(BASE + '/api/kb/ingest', params=H, json={'title': 'dirty', 'content': dirty, 'source_kind': 'paste', 'apply_clean': True}, timeout=180)
    print('ingest', rb.status_code, rb.text[:400])
    rb.raise_for_status()
    st = status(H)
    clean_all(H, st)

    # C upload md
    md = '# 周报\n' + dirty
    rc = requests.post(BASE + '/api/kb/upload', params=H, files={'file': ('dirty.md', md, 'text/markdown')}, timeout=120)
    print('upload', rc.status_code, rc.text[:400])
    rc.raise_for_status()
    st = status(H)
    clean_all(H, st)
    print('OK')

if __name__ == '__main__':
    main()
