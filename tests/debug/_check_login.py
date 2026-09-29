"""验证 login 路由是否正常工作"""
import sys, os, requests
sys.path.insert(0, '.')
sys.path = [p for p in sys.path if 'hermes-agent' not in p]
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

BASE = 'http://127.0.0.1:8123'

try:
    r = requests.post(BASE + '/api/login', json={'username': '尼古喵喵', 'password': '123456', 'role': 'personal'}, timeout=30)
    print('status', r.status_code)
    print('headers', dict(r.headers))
    print('body', r.text[:500])
except Exception as e:
    print('ERR', e)
