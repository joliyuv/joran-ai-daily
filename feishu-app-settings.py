"""Loopback setup for the application bot; saving never sends a message."""
from html import escape
import importlib.util
import json
import os
import re
import secrets
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('existing_settings', ROOT/'feishu-settings.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
ORIGIN = 'http://127.0.0.1:8769'
TOKEN = secrets.token_urlsafe(32)
CONFIG = base.CONFIG_DIR/'feishu-app.json'

def save(values):
    app_id = values.get('app_id', '').strip()
    secret = values.get('app_secret', '').strip()
    chat_id = values.get('test_chat_id', '').strip()
    previous = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    retained = {k:previous[k] for k in ('delivery_chat_id','delivery_chat_name','customer_chat_id','customer_chat_name','delivery_targets')
                if k in previous} if previous.get('app_id') == app_id else {}
    if not chat_id and previous.get('app_id') == app_id:
        chat_id = previous.get('test_chat_id', '')
    if not re.fullmatch(r'cli_[A-Za-z0-9]+', app_id):
        raise ValueError('App ID 格式不正确')
    if not secret or len(secret)>512:
        raise ValueError('请填写 App Secret')
    if chat_id and not re.fullmatch(r'oc_[A-Za-z0-9]+', chat_id):
        raise ValueError('群 ID 应以 oc_ 开头')
    base.CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.CONFIG_DIR.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix='.feishu-app-', dir=base.CONFIG_DIR)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump({'app_id':app_id,'app_secret':secret,'test_chat_id':chat_id,
                       'test_chat_name':'JORAN AI 日報｜測試', **retained},f,ensure_ascii=False)
        os.replace(temporary, CONFIG)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)

class Handler(base.Handler):
    def valid_host(self):
        return self.headers.get('Host')=='127.0.0.1:8769'
    def respond(self, code, body, mime='application/json'):
        # The inherited response policy uses this same nonce for CSP.
        base.TOKEN=TOKEN
        super().respond(code,body,mime)
    def do_GET(self):
        if not self.valid_host():return self.respond(403,'{}')
        if self.path!='/':return self.respond(404,'{}')
        css=(ROOT/'feishu-settings.html').read_text().split('<style>',1)[1].split('</style>',1)[0]
        page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>应用机器人 · 本地设置</title><style>'''+css+'''</style><main><h1>连接应用机器人</h1><p>只保存在这台电脑；保存不会发送消息，不会更改客户群的 webhook。</p><form id="form" autocomplete="off"><label class="field" for="app">App ID</label><input id="app" type="text" value="LOCAL_APP_ID" required><label class="field" for="secret">App Secret</label><input id="secret" type="password" autocomplete="off" required><small class="hint">在飞书开发者后台「凭证与基础信息」复制。不要发到聊天里。</small><label class="field" for="chat">测试群 ID（可稍后补充）</label><input id="chat" type="text" placeholder="oc_…"><small class="hint">只用于「JORAN AI 日報｜測試」。填群 ID，不填 webhook 或邀请链接。</small><button>保存到本机</button><p id="feedback" role="status"></p></form></main><script nonce="'''+TOKEN+'''">document.querySelector('#form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Token':'TOKEN'},body:JSON.stringify({app_id:document.querySelector('#app').value,app_secret:document.querySelector('#secret').value,test_chat_id:document.querySelector('#chat').value})});const d=await r.json();document.querySelector('#feedback').textContent=d.saved?'已安全保存，尚未发送消息。':d.error; if(d.saved)document.querySelector('#secret').value='';}catch(_){document.querySelector('#feedback').textContent='保存失败，请重试。';}finally{b.disabled=false;}};</script></html>'''
        try:
            saved_app_id = json.loads(CONFIG.read_text()).get('app_id', '') if CONFIG.exists() else ''
        except (ValueError, OSError):
            saved_app_id = ''
        page = page.replace('LOCAL_APP_ID', escape(str(saved_app_id), quote=True))
        self.respond(200,page.replace("'X-Setup-Token':'TOKEN'", "'X-Setup-Token':'"+TOKEN+"'"),'text/html')
    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin')!=ORIGIN or not secrets.compare_digest(self.headers.get('X-Setup-Token',''),TOKEN):
            return self.respond(403,'{}')
        if self.path!='/save':return self.respond(404,'{}')
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=8192:raise ValueError('内容为空或过长')
            v=json.loads(self.rfile.read(size))
            if not isinstance(v,dict) or any(not isinstance(v.get(k,''),str) for k in ['app_id','app_secret','test_chat_id']):raise ValueError('格式不正确')
            save(v)
        except (ValueError,UnicodeError):return self.respond(400,'{"error":"请检查 App ID、App Secret 和群 ID 的填写格式。"}')
        except OSError:return self.respond(500,'{"error":"本地保存失败。"}')
        self.respond(200,'{"saved":true}')

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',8769),Handler)
    print('应用机器人本地设置：'+ORIGIN,flush=True)
    server.serve_forever()
