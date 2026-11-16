"""Deliver a dated, approved issue with durable duplicate protection."""
import argparse
import base64
from datetime import datetime
import fcntl
import hashlib
import hmac
import json
import errno
from pathlib import Path
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

from newsletter import ROOT, issue_hash, markdown, read_issue, write_json
from sources import CHINA, timestamp

PRIVATE = ROOT.parents[1] / '.private' / 'newsletter'
HOOK = re.compile(r'https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]{8,200}')


class DeliveryFailure(ValueError):
    """Only fixed, safe diagnostic fields may leave the delivery boundary."""
    def __init__(self, status, category, part=None):
        self.result = {'status': status, 'errorCategory': category,
                       'automaticRetry': False}
        if part is not None:
            self.result['part'] = part
        super().__init__(category)


def error_category(error):
    """Never log str(error), request URLs, response bodies, or credentials."""
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return 'network_timeout'
    if isinstance(reason, socket.gaierror):
        return 'dns_failure'
    if isinstance(reason, ssl.SSLError):
        return 'tls_failure'
    if isinstance(reason, PermissionError) or getattr(reason, 'errno', None) in {errno.EPERM, errno.EACCES}:
        return 'network_permission_denied'
    if isinstance(reason, ConnectionRefusedError):
        return 'connection_refused'
    if isinstance(reason, (ConnectionResetError, BrokenPipeError)):
        return 'connection_interrupted'
    if isinstance(error, (ValueError, TypeError, AttributeError)):
        return 'invalid_response'
    return 'network_error' if isinstance(error, (URLError, OSError)) else 'local_error'


def card_payloads(issue, keyword='', image_keys=None):
    """Split at complete section boundaries, before any request is sent."""
    if any(article.get('image') for article in issue['articles']):
        from feishu_cards import image_card_payloads
        return image_card_payloads(issue, keyword, image_keys or {})
    chunks, current = [], ''
    for section in markdown(issue).split('\n---\n'):
        combined = current + ('\n---\n' if current else '') + section
        if len(combined.encode()) > 14000:
            if not current or len(section.encode()) > 14000:
                raise ValueError('单个新闻段落过长，请先缩短正文，尚未发送')
            chunks.append(current)
            current = section
        else:
            current = combined
    if current:
        chunks.append(current)
    result = []
    for index, content in enumerate(chunks, 1):
        title = f"每日 AI · {issue['date']}"
        if keyword and keyword not in title:
            title = keyword + ' · ' + title
        if len(chunks) > 1:
            title += f'（{index}/{len(chunks)}）'
        payload = {'msg_type': 'interactive', 'card': {'schema': '2.0',
            'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': 'blue'},
            'body': {'elements': [{'tag': 'markdown', 'content': content}]}}}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 19000:
            raise ValueError('消息超过本地大小限制，尚未发送')
        result.append(payload)
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request_feishu(webhook, payload):
    request = Request(webhook, data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    with build_opener(NoRedirect).open(request, timeout=25) as response:
        return json.loads(response.read(100000))


def deliver(issue, config, directory, sender=request_feishu):
    if issue['status'] != 'approved' or issue.get('approval', {}).get('contentHash') != issue_hash(issue):
        raise ValueError('日报未确认，或确认后正文已更改')
    webhook = config.get('webhook', '')
    if not HOOK.fullmatch(webhook):
        raise ValueError('本地 webhook 配置格式不正确')
    if config.get('signature_enabled') and not config.get('secret'):
        raise ValueError('缺少签名密钥')
    payloads = card_payloads(issue, config.get('keyword', ''))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = hashlib.sha256(webhook.encode()).hexdigest()[:16]
    path = directory / (issue['id'] + '-' + target + '.json')
    with open(directory / '.send.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('另一个发送正在进行，未重复发送')
        ledger = json.loads(path.read_text()) if path.exists() else {
            'issueId': issue['id'], 'contentHash': issue_hash(issue), 'targetHash': target,
            'parts': [{'status': 'pending'} for _ in payloads]}
        if ledger['contentHash'] != issue_hash(issue) or len(ledger['parts']) != len(payloads):
            raise ValueError('本期已有其他版本的投递记录，未覆盖或重复发送')
        if any(part['status'] in {'in_flight', 'unknown'} for part in ledger['parts']):
            raise DeliveryFailure('unknown', 'prior_result_unconfirmed')
        if all(part['status'] == 'accepted' for part in ledger['parts']):
            return {'status': 'already_sent', 'parts': len(payloads)}
        for index, payload in enumerate(payloads):
            part = ledger['parts'][index]
            if part['status'] == 'accepted':
                continue
            if config.get('signature_enabled'):
                epoch = str(int(time.time()))
                key = (epoch + '\n' + config['secret']).encode()
                payload['timestamp'] = epoch
                payload['sign'] = base64.b64encode(hmac.new(key, b'', hashlib.sha256).digest()).decode()
            part.update(status='in_flight', attemptedAt=timestamp())
            write_json(path, ledger)
            try:
                response = sender(webhook, payload)
                code = response.get('code', response.get('StatusCode'))
                if type(code) is not int:
                    raise ValueError('invalid response code')
                part.update(status='accepted' if code == 0 else 'rejected', code=code,
                            completedAt=timestamp())
                part.pop('errorCategory', None)
                part.pop('httpStatus', None)
                if code != 0:
                    part['errorCategory'] = 'feishu_rejected'
            except HTTPError as error:
                part.update(status='rejected' if 400 <= error.code < 500 else 'unknown',
                            httpStatus=error.code, completedAt=timestamp(),
                            errorCategory='http_rate_limited' if error.code == 429 else (
                                'http_client_error' if 400 <= error.code < 500 else 'http_server_or_redirect_error'))
            except (URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError) as error:
                part.update(status='unknown', completedAt=timestamp(), errorCategory=error_category(error))
            write_json(path, ledger)
            if part['status'] != 'accepted':
                raise DeliveryFailure(part['status'], part['errorCategory'], index + 1)
        return {'status': 'accepted', 'parts': len(payloads)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('issue', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--scheduled', action='store_true', help='只允许北京时间 10:00–10:10 发送本期')
    args = parser.parse_args()
    issue = read_issue(args.issue)
    now = datetime.now(CHINA)
    if issue['date'] != now.date().isoformat():
        raise ValueError('只允许发送北京时间今天的一期，不能用旧日报补位')
    if args.scheduled and not (now.hour == 10 and now.minute < 10):
        raise ValueError('不在北京时间 10:00–10:10 发送窗口，未提前或延迟补发')
    if args.dry_run:
        print(json.dumps({'dryRun': True, 'issueId': issue['id'],
                          'parts': len(card_payloads(issue)), 'contentHash': issue_hash(issue)}))
        return
    config = json.loads((PRIVATE / 'feishu.json').read_text())
    print(json.dumps(deliver(issue, config, PRIVATE / 'deliveries'), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except DeliveryFailure as error:
        print(json.dumps(error.result, ensure_ascii=False))
        raise SystemExit(1)
    except (ValueError, KeyError, OSError):
        # Never expose a credential-bearing request URL in an exception or log.
        print(json.dumps({'status': 'blocked', 'errorCategory': 'local_validation_or_storage_error',
                          'automaticRetry': False}))
        raise SystemExit(1)
