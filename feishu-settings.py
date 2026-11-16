"""Loopback-only credential setup. Does not contact Feishu or send messages."""
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT.parents[1] / '.private' / 'newsletter'
ADDRESS = ('127.0.0.1', 8768)
ORIGIN = 'http://127.0.0.1:8768'
TOKEN = secrets.token_urlsafe(32)
HOOK = re.compile(r'https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]{8,200}')


def load_config():
    path = CONFIG_DIR / 'feishu.json'
    return json.loads(path.read_text()) if path.exists() else {}


def save_config(values):
    old = load_config()
    webhook = values.get('webhook', '').strip() or old.get('webhook', '')
    signature = values.get('signature_enabled') is True
    entered_secret = values.get('secret', '').strip()
    bot_changed = bool(old.get('webhook')) and webhook != old['webhook']
    if bot_changed and signature and not entered_secret:
        raise ValueError('机器人地址已更换，请重新填写新机器人的签名密钥。')
    secret = entered_secret or (old.get('secret', '') if not bot_changed else '')
    keyword = values.get('keyword', '').strip()
    if not HOOK.fullmatch(webhook):
        raise ValueError('请粘贴完整的飞书机器人 webhook 地址，以 https://open.feishu.cn/open-apis/bot/v2/hook/ 开头。')
    if signature and not secret:
        raise ValueError('已勾选签名校验，请填写飞书机器人里的签名密钥。')
    if len(secret) > 512 or len(keyword) > 100:
        raise ValueError('密钥或关键词过长，请检查填写内容。')
    result = {'webhook': webhook, 'signature_enabled': signature,
              'secret': secret if signature else '', 'keyword': keyword,
              'ip_allowlist_enabled': values.get('ip_allowlist_enabled') is True}
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    CONFIG_DIR.chmod(0o700)
    (CONFIG_DIR / '.gitignore').write_text('*\n')
    fd, temporary = tempfile.mkstemp(prefix='.feishu-', dir=CONFIG_DIR)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, CONFIG_DIR / 'feishu.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log form bodies, credentials, or URLs.

    def respond(self, code, body, mime='application/json'):
        data = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-" + TOKEN + "'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        return self.headers.get('Host') == '127.0.0.1:8768'

    def do_GET(self):
        if not self.valid_host():
            self.respond(403, '{}')
        elif self.path == '/':
            page = (ROOT / 'feishu-settings.html').read_text().replace('__NONCE__', TOKEN)
            self.respond(200, page, 'text/html')
        elif self.path == '/status':
            try:
                config = load_config()
                self.respond(200, json.dumps({'configured': bool(config.get('webhook')),
                    'signature_enabled': bool(config.get('signature_enabled')),
                    'has_secret': bool(config.get('secret')), 'keyword': config.get('keyword', ''),
                    'ip_allowlist_enabled': bool(config.get('ip_allowlist_enabled'))}))
            except (OSError, ValueError):
                self.respond(500, '{"error":"本地配置无法读取，请检查配置文件。"}')
        else:
            self.respond(404, '{}')

    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin') != ORIGIN or not secrets.compare_digest(self.headers.get('X-Setup-Token', ''), TOKEN):
            self.respond(403, '{}')
            return
        if self.path != '/save':
            self.respond(404, '{}')
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192:
                raise ValueError('填写内容过长或为空。')
            values = json.loads(self.rfile.read(size))
            if not isinstance(values, dict) or any(not isinstance(values.get(k, ''), str) for k in ('webhook', 'secret', 'keyword')):
                raise ValueError('配置格式不正确，请重新填写。')
            save_config(values)
        except (ValueError, UnicodeError) as error:
            message = str(error) if type(error) is ValueError else '配置格式不正确，请重新填写。'
            self.respond(400, json.dumps({'error': message}))
            return
        except OSError:
            self.respond(500, '{"error":"保存失败，请检查本地目录写入权限。"}')
            return
        self.respond(200, '{"saved":true}')


if __name__ == '__main__':
    try:
        server = HTTPServer(ADDRESS, Handler)
    except OSError:
        print('8768 端口已被占用。如果配置入口已启动，请直接打开 ' + ORIGIN, flush=True)
        raise SystemExit(1)
    print('飞书本地配置：' + ORIGIN + '（只保存配置，不发送消息）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
