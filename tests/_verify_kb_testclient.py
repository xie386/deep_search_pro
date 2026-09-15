"""直接调函数验证三条入库链路（含评估+清洗）。"""
import sys, os, json
sys.path.insert(0, '.')
sys.path = [p for p in sys.path if 'hermes-agent' not in p]
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

from fastapi.testclient import TestClient
import api.server as srv
app = srv.app
client = TestClient(app)

r = client.post('/api/login', json={'username': '尼古喵喵', 'password': '123456', 'role': 'personal'})
print('login:', r.status_code, r.text[:200])
assert r.status_code == 200
tok = r.json()['token']
H = {'token': tok}

dirty = '奴婢给主人整理的呢～(≧∇≦)ﾉ\n| A | B |\n| --- | --- |\n| 1 | 2 |\n昇腾 910C 良率爬坡顺利。'

# 评估
r = client.post('/api/kb/evaluate', params=H, json={'title': 'dirty', 'content': dirty})
print('evaluate:', r.status_code)
print('  body keys:', list(r.json().keys()))
print('  evaluation:', r.json().get('evaluation', {}).get('suggestion', '')[:100])
print('  needs_clean:', r.json().get('needs_clean'))

# ingest (新版含评估+清洗)
r = client.post('/api/kb/ingest', params=H, json={'title': 'dirty', 'content': dirty, 'source_kind': 'paste', 'apply_clean': True})
print('ingest:', r.status_code)
b = r.json()
print('  body keys:', list(b.keys()))
print('  ok:', b.get('ok'), 'dup:', b.get('dup'))
print('  needs_clean:', b.get('needs_clean'), 'cleaned_preview[:80]:', b.get('cleaned_preview', '')[:80])

# 清理
st = client.get('/api/kb/status', params=H).json()
print('status.docs:', [(d['title'], d.get('chunks')) for d in st.get('docs', [])])
for d in st.get('docs', []):
    client.delete('/api/kb/docs/' + d['source_id'], params=H)

# upload
md = '# 周报\n' + dirty
r = client.post('/api/kb/upload', params=H, files={'file': ('dirty.md', md, 'text/markdown')})
print('upload:', r.status_code)
b = r.json()
print('  body keys:', list(b.keys()))
print('  needs_clean:', b.get('needs_clean'), 'evaluation.suggestion:', b.get('evaluation', {}).get('suggestion', '')[:80])

# 清理
st = client.get('/api/kb/status', params=H).json()
for d in st.get('docs', []):
    client.delete('/api/kb/docs/' + d['source_id'], params=H)

print('=== ALL OK ===')
