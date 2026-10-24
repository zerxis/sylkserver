# Run inside the SylkServer image: PYTHONPATH=<repo> python3 tests/test_session_transfer.py
# Exercises the session-transfer additions with SylkServer's real model classes.
import re
from sipsimple.core import SIPCoreError
from sylk.applications.webrtcgateway.models import validators

# sipsimple's SIPURI.parse needs a running SIP engine; stand in a strict regex.
class _SIPURI:
    @staticmethod
    def parse(uri):
        if not re.match(r'^sips?:[^@\s]+@[A-Za-z0-9.-]+(:\d+)?$', uri):
            raise SIPCoreError('Not a valid SIP URI: %s' % uri)
        return uri
validators.SIPURI = _SIPURI

from sylk.applications.webrtcgateway.models import janus, sylkrtc
from sylk.applications.webrtcgateway.models.janus import SIPDataHandler
from sylk.applications.webrtcgateway.handler import parse_sipfrag

failures = 0
def check(name, cond):
    global failures
    print(('ok   ' if cond else 'FAIL ') + name)
    failures += 0 if cond else 1

# browser request parsing
req = sylkrtc.SylkRTCRequest.from_message({'sylkrtc': 'session-transfer', 'transaction': 't1', 'session': 's1', 'uri': 'sip:2222222@callcenter.africa'})
check('blind request parses', isinstance(req, sylkrtc.SessionTransferRequest) and req.uri == '2222222@callcenter.africa' and req.replace_session is None)
req = sylkrtc.SylkRTCRequest.from_message({'sylkrtc': 'session-transfer', 'transaction': 't2', 'session': 's1', 'uri': 'consult-2222222@callcenter.africa', 'replace_session': 's2'})
check('attended request parses', req.replace_session == 's2')
try:
    sylkrtc.SylkRTCRequest.from_message({'sylkrtc': 'session-transfer', 'transaction': 't3', 'session': 's1', 'uri': 'not a uri @@'})
    check('bad uri rejected', False)
except Exception:
    check('bad uri rejected', True)

# Janus request
msg = janus.SIPTransfer(uri='2222222@callcenter.africa', replace='abc@host').__data__
check('janus transfer message', msg == {'request': 'transfer', 'uri': 'sip:2222222@callcenter.africa', 'replace': 'abc@host'})
check('janus blind message has no replace', 'replace' not in janus.SIPTransfer(uri='2222222@x.y').__data__)

# Janus events decode
ev = SIPDataHandler.decode({'sip': 'event', 'result': {'event': 'transferring'}})
check('transferring decodes', isinstance(ev, janus.SIPTransferringEvent))
ev = SIPDataHandler.decode({'sip': 'event', 'call_id': 'c1', 'result': {'event': 'notify', 'notify': 'refer', 'substate': 'terminated', 'content-type': 'message/sipfrag', 'content': 'SIP/2.0 200 OK\r\n'}})
check('notify decodes', isinstance(ev, janus.SIPNotifyEvent) and ev.result.notify == 'refer' and ev.result.content.startswith('SIP/2.0 200'))
check('handler id for notify', ev.__id__ == ('sip', 'event', 'notify'))

# browser event
out = sylkrtc.SessionTransferEvent(session='s1', state='failed', code=403, reason='Forbidden').__data__
check('transfer event', out == {'sylkrtc': 'session-event', 'session': 's1', 'event': 'transfer', 'state': 'failed', 'code': 403, 'reason': 'Forbidden'})

# sipfrag
check('sipfrag 200', parse_sipfrag('SIP/2.0 200 OK\r\n') == (200, 'OK'))
check('sipfrag 100', parse_sipfrag('SIP/2.0 100 Trying') == (100, 'Trying'))
check('sipfrag 403', parse_sipfrag('SIP/2.0 403 Forbidden') == (403, 'Forbidden'))
check('sipfrag junk', parse_sipfrag('hello') == (None, None) and parse_sipfrag(None) == (None, None))

# handler methods, on a fake connection
from types import SimpleNamespace
from sylk.applications.webrtcgateway import handler as h
cls = next(c for c in vars(h).values() if isinstance(c, type) and hasattr(c, '_RH_session_transfer'))

class FakeHandle:
    def __init__(self): self.calls = []
    def transfer(self, uri, replace=None): self.calls.append((uri, replace))

def sess(id, state='established', call_id=None):
    return SimpleNamespace(id=id, direction='incoming', state=state, call_id=call_id, janus_handle=FakeHandle())

a, b = sess('s1'), sess('s2', call_id='callB@fs')
sent = []
fake = SimpleNamespace(sip_sessions={'s1': a, 's2': b, 101: a}, log=SimpleNamespace(info=lambda *x: None), send=sent.append)

cls._RH_session_transfer(fake, sylkrtc.SessionTransferRequest(transaction='t', session='s1', uri='2222222@x.y'))
check('blind -> janus transfer', a.janus_handle.calls == [('2222222@x.y', None)])
cls._RH_session_transfer(fake, sylkrtc.SessionTransferRequest(transaction='t', session='s1', uri='consult-2222222@x.y', replace_session='s2'))
check('attended -> replace call-id', a.janus_handle.calls[-1] == ('consult-2222222@x.y', 'callB@fs'))

for label, req in [
    ('unknown session', dict(session='nope', uri='1@x.y')),
    ('unknown replace session', dict(session='s1', uri='1@x.y', replace_session='nope')),
]:
    try:
        cls._RH_session_transfer(fake, sylkrtc.SessionTransferRequest(transaction='t', **req)); check(label, False)
    except h.APIError:
        check(label + ' -> APIError', True)
b.call_id = None
try:
    cls._RH_session_transfer(fake, sylkrtc.SessionTransferRequest(transaction='t', session='s1', uri='1@x.y', replace_session='s2')); check('no call-id', False)
except h.APIError:
    check('replace without call-id -> APIError', True)
a.state = 'connecting'
try:
    cls._RH_session_transfer(fake, sylkrtc.SessionTransferRequest(transaction='t', session='s1', uri='1@x.y')); check('ringing', False)
except h.APIError:
    check('not established -> APIError', True)

def notify(content, kind='refer'):
    data = SIPDataHandler.decode({'sip': 'event', 'result': {'event': 'notify', 'notify': kind, 'content': content}})
    return SimpleNamespace(sender=101, plugindata=SimpleNamespace(data=data))
cls._EH_janus_sip_event_notify(fake, notify('SIP/2.0 100 Trying'))
cls._EH_janus_sip_event_notify(fake, notify('SIP/2.0 200 OK'))
cls._EH_janus_sip_event_notify(fake, notify('SIP/2.0 403 Forbidden'))
cls._EH_janus_sip_event_notify(fake, notify('1 message', kind='message-summary'))
check('notify -> transfer events', [(e.state, e.code) for e in sent] == [('progress', 100), ('completed', 200), ('failed', 403)])
cls._EH_janus_sip_event_transferring(fake, SimpleNamespace(sender=101))
check('transferring -> started', sent[-1].state == 'started' and sent[-1].session == 's1')

print('%d failures' % failures)
raise SystemExit(1 if failures else 0)
