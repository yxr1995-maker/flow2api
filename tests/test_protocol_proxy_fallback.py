import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from unittest.mock import AsyncMock
import src.api.admin as admin
import src.services.protocol_login as pl
from src.core.models import Token
from src.services.token_manager import TokenManager
out = []
def check(name, cond, extra=''):
    out.append(('PASS' if cond else 'FAIL') + ' ' + name + (' | ' + str(extra) if extra else ''))
class PM:
    def __init__(self, v=None, e=None):
        self.v = v
        self.e = e
        self.n = 0
    async def get_request_proxy_url(self):
        self.n += 1
        if self.e:
            raise self.e
        return self.v
async def main():
    async def ok_login(google_cookies, proxy=None, email=None):
        ok_login.got = proxy
        return {'success': True, 'session_token': 'STUB_ST'}
    admin.protocol_loginer.login = ok_login
    pl.protocol_loginer.login = ok_login
    admin._verify_plugin_connection_token = AsyncMock(return_value=None)
    admin.db = SimpleNamespace(get_plugin_config=AsyncMock(return_value=SimpleNamespace(auto_enable_on_update=False)), get_token_by_email=AsyncMock(return_value=None))
    admin.token_manager = SimpleNamespace(flow_client=SimpleNamespace(st_to_at=AsyncMock(return_value={'access_token': 'AT', 'expires': None, 'user': {'email': 't@example.com'}})), add_token=AsyncMock(return_value=SimpleNamespace(id=7, email='t@example.com')), update_token=AsyncMock())
    pm = PM(v='http://fallback:8080')
    admin.proxy_manager = pm
    r = await admin.plugin_update_token({'google_cookies': 'SID=x'}, 'auth')
    check('admin fallback used', r.get('success') is True and ok_login.got == 'http://fallback:8080', ok_login.got)
    async def no_login(**kw):
        no_login.called = True
        return {'success': True, 'session_token': 'X'}
    no_login.called = False
    admin.protocol_loginer.login = no_login
    pl.protocol_loginer.login = no_login
    admin.proxy_manager = PM(e=RuntimeError('store down'))
    try:
        await admin.plugin_update_token({'google_cookies': 'SID=x'}, 'auth')
        check('admin provider failure 500', False, 'no raise')
    except Exception as e:
        check('admin provider failure 500', getattr(e, 'status_code', None) == 500 and getattr(e, 'detail', '') == 'PROXY_CONFIG_UNAVAILABLE', repr(e))
    check('admin provider failure no login', no_login.called is False)
    async def rlogin(google_cookies, proxy=None, email=None):
        rlogin.got = proxy
        return {'success': True, 'session_token': 'NEW_ST'}
    pl.protocol_loginer.login = rlogin
    upd = {}
    async def fake_update(tid, **kw):
        upd.update(kw)
    pm3 = PM(v='http://fallback:8080')
    mgr = TokenManager(db=SimpleNamespace(update_token=fake_update), flow_client=SimpleNamespace(proxy_manager=pm3))
    tok = Token(st='OLD', email='t@example.com', protocol_mode='protocol', google_cookies='SID=x', proxy_url='http://token:8080')
    res = await mgr._try_protocol_refresh_st(9, tok)
    check('refresh per-token used', res == 'NEW_ST' and rlogin.got == 'http://token:8080' and pm3.n == 0, rlogin.got)
    rlogin.got = 'CALLED'
    upd.clear()
    pm4 = PM(e=RuntimeError('store down'))
    mgr2 = TokenManager(db=SimpleNamespace(update_token=fake_update), flow_client=SimpleNamespace(proxy_manager=pm4))
    tok2 = Token(st='OLD', email='t@example.com', protocol_mode='protocol', google_cookies='SID=x', proxy_url='')
    res2 = await mgr2._try_protocol_refresh_st(9, tok2)
    check('refresh provider failure none', res2 is None and rlogin.got == 'CALLED' and upd.get('last_st_refresh_result') == 'PROXY_CONFIG_UNAVAILABLE', upd.get('last_st_refresh_result'))
asyncio.run(main())
print(chr(10).join(out))
if any(line.startswith('FAIL') for line in out):
    raise SystemExit(1)
