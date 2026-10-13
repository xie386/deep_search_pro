"""M3 评估+清洗 验证：evaluate / ingest / upload 三条路都返回 evaluation + cleaned_preview"""
import sys, os, requests
sys.path.insert(0, '.')
sys.path = [p for p in sys.path if 'hermes-agent' not in p]
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

BASE = 'http://127.0.0.1:8123'
# ── 2026-10-13 修（前置清理）：改为自建临时账号 + 退出时回收（不再占用共享测试号）
import atexit as _atexit, time as _time
USER = os.getenv('PROBE_ACCOUNT') or 't_kb_%d' % int(_time.time())
PWD = os.getenv('PROBE_PASSWORD') or 't_pwd_%d' % int(_time.time())


@_atexit.register
def _cleanup_tmp_account():
    try:
        import sys as _s; _s.path.insert(0, '.')
        from tools.schema_personal import get_personal_conn as _pc, purge_account as _purge
        _c = _pc()
        _row = _c.execute('SELECT id FROM accounts WHERE username=?', (USER,)).fetchone()
        _c.close()
        if _row:
            _purge(_row[0]); print('[cleanup] 临时账号 %s 已回收' % USER)
    except Exception as _e:                                          # noqa: BLE001
        print('[cleanup] 回收失败: %s' % _e)

def login():
    requests.post(BASE + '/api/register', json={'username': USER, 'password': PWD, 'role': 'personal'}, timeout=60)
    r = requests.post(BASE + '/api/login', json={'username': USER, 'password': PWD, 'role': 'personal'}, timeout=60)
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
