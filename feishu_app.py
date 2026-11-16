"""Application-bot delivery, isolated from the legacy webhook sender.

Persists receipts before querying; uncertain sends are never repeated here.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import secrets
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from feishu import PRIVATE, NoRedirect, DeliveryFailure, card_payloads, error_category
from newsletter import read_issue, issue_hash, write_json
from sources import CHINA, timestamp
from datetime import datetime

API = 'https://open.feishu.cn/open-apis/'


def scheduled_time_allowed(now, workflow):
    """Catch-up stays on the issue's current day; the CLI checks its date separately."""
    if workflow.get('allowSameDayCatchUp') is True:
        return now.hour >= 10
    return (10, 0) <= (now.hour, now.minute) < (10, 11)


def upload_image(path, config, directory):
    """Upload a local PNG/JPEG/WebP, caching its key by app and image bytes."""
    raw = Path(path).read_bytes()
    mime = ('image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else
            'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else
            'image/webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else None)
    if not mime or not 0 < len(raw) < 10_000_000:
        raise DeliveryFailure('blocked', 'invalid_image')
    digest = hashlib.sha256(config['app_id'].encode() + raw).hexdigest()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    cache = directory / (digest + '.json')
    if cache.exists():
        return json.loads(cache.read_text())['imageKey']
    token = request('POST', 'auth/v3/tenant_access_token/internal', {
        'app_id': config['app_id'], 'app_secret': config['app_secret']})['tenant_access_token']
    boundary = 'newsletter-' + secrets.token_hex(20)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="image.{mime.split("/")[1]}"\r\n'
            f'Content-Type: {mime}\r\n\r\n').encode() + raw + f'\r\n--{boundary}--\r\n'.encode()
    try:
        req = Request(API + 'im/v1/images', data=body, method='POST', headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
        with build_opener(NoRedirect).open(req, timeout=30) as response:
            result = json.loads(response.read(100000))
        if result.get('code') != 0:
            raise DeliveryFailure('rejected', 'image_upload_api_' + str(result.get('code')))
        key = result.get('data', {}).get('image_key', '')
        if not re.fullmatch(r'img_[A-Za-z0-9_-]+', key):
            raise DeliveryFailure('blocked', 'image_key_missing')
    except DeliveryFailure:
        raise
    except Exception as error:
        raise DeliveryFailure('blocked', 'image_upload_' + error_category(error)) from None
    write_json(cache, {'imageKey': key, 'uploadedAt': timestamp()})
    return key


def request(method, path, data=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        req = Request(API + path, data=None if data is None else json.dumps(data, ensure_ascii=False).encode(),
                      headers=headers, method=method)
        with build_opener(NoRedirect).open(req, timeout=25) as response:
            result = json.loads(response.read(1_000_000))
        if not isinstance(result, dict) or type(result.get('code')) is not int:
            raise ValueError('invalid response')
        if result['code'] != 0:
            raise DeliveryFailure('rejected', 'feishu_api_' + str(result['code']))
        return result
    except DeliveryFailure:
        raise
    except HTTPError as error:
        raise DeliveryFailure('unknown', 'http_' + str(error.code)) from None
    except Exception as error:
        raise DeliveryFailure('unknown', error_category(error)) from None


def verify_message(message, chat_id, app_id, card):
    if message.get('chat_id') != chat_id or message.get('deleted') is not False:
        return False
    sender = message.get('sender', {})
    if sender.get('id') != app_id or sender.get('sender_type') != 'app':
        return False
    content = message.get('body', {}).get('content', '')
    try:
        decoded = json.loads(content)
    except (TypeError, ValueError):
        return False
    strings = []
    def visit(value):
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for item in value.values(): visit(item)
        elif isinstance(value, list):
            for item in value: visit(item)
    visit(decoded)
    # Feishu returns literal backslash-n and converts Markdown rules to <hr>.
    # Normalize only presentation; still compare all article text and URLs.
    def normalize(value):
        value = value.replace('\\n', '\n')
        value = re.sub(r'<hr\s*/?>|(?m:^---$)', '', value)
        return re.sub(r'\s+', '', value)
    text = normalize('\n'.join(strings))
    expected = [e['content'] for e in card['body']['elements'] if e['tag'] == 'markdown']
    image_keys = [e['img_key'] for e in card['body']['elements'] if e['tag'] == 'img']
    return (bool(expected) and all(normalize(part) in text for part in expected)
            and all(key in strings for key in image_keys))


def deliver_to_group(issue, config, directory, transport=request, image_keys=None, *, scheduled=False):
    if issue['status'] != 'approved' or issue.get('approval', {}).get('contentHash') != issue_hash(issue):
        raise DeliveryFailure('blocked', 'issue_not_approved')
    app = config.get('app_id', '')
    chat = config.get('delivery_chat_id' if scheduled else 'test_chat_id', '')
    if not re.fullmatch(r'cli_[A-Za-z0-9]+', app) or not re.fullmatch(r'oc_[A-Za-z0-9]+', chat) or not config.get('app_secret'):
        raise DeliveryFailure('blocked', 'test_config_incomplete')
    payloads = card_payloads(issue, '' if scheduled else '【测试】', image_keys=image_keys)
    digest = hashlib.sha256((app + chat + issue_hash(issue) + ('daily' if scheduled else '')).encode()).hexdigest()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / (issue['id'] + '-' + digest[:16] + '.json')
    with open(directory / '.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeliveryFailure('blocked', 'another_delivery_running') from None
        ledger = json.loads(path.read_text()) if path.exists() else {
            'contentHash': issue_hash(issue), 'parts': [{'status': 'pending'} for _ in payloads]}
        if len(ledger['parts']) != len(payloads) or ledger['contentHash'] != issue_hash(issue):
            raise DeliveryFailure('blocked', 'ledger_mismatch')
        if all(p['status'] == 'verified' for p in ledger['parts']):
            return {'status': 'already_verified', 'parts': len(payloads)}
        auth = transport('POST', 'auth/v3/tenant_access_token/internal', {
            'app_id': app, 'app_secret': config['app_secret']})
        token = auth.get('tenant_access_token')
        if not isinstance(token, str) or not token:
            raise DeliveryFailure('blocked', 'access_token_missing')
        for index, payload in enumerate(payloads):
            part = ledger['parts'][index]
            if part['status'] == 'verified': continue
            card = payload['card']
            # An explicit API rejection confirms this attempt was not accepted.
            # Transport failures / missing receipts remain uncertain and never reset.
            if (part['status'] == 'rejected' and not part.get('messageId')
                    and part.get('errorCategory', '').startswith('feishu_api_')):
                part['status'] = 'pending'
            # Tracking stays in the private ledger/uuid; never append it to a card.
            if part['status'] == 'pending':
                part.update(status='in_flight', attemptedAt=timestamp(), uuid=digest[:36] + '-' + str(index))
                write_json(path, ledger)
                try:
                    response = transport('POST', 'im/v1/messages?receive_id_type=chat_id', {
                        'receive_id': chat, 'msg_type': 'interactive',
                        'content': json.dumps(card, ensure_ascii=False), 'uuid': part['uuid']}, token)
                    mid = response.get('data', {}).get('message_id', '')
                    if not re.fullmatch(r'om_[A-Za-z0-9]+', mid):
                        raise DeliveryFailure('unknown', 'message_receipt_missing')
                    part.update(status='accepted', messageId=mid)
                except DeliveryFailure as error:
                    part.update(status=error.result['status'], errorCategory=error.result['errorCategory'])
                    write_json(path, ledger)
                    raise
                write_json(path, ledger)
            if not part.get('messageId'):
                raise DeliveryFailure('unknown', 'prior_send_requires_reconciliation')
            response = transport('GET', 'im/v1/messages/' + part['messageId'] + '?card_msg_content_type=user_card_content', token=token)
            items = response.get('data', {}).get('items', [])
            if len(items) != 1 or items[0].get('message_id') != part['messageId'] or not verify_message(items[0], chat, app, card):
                raise DeliveryFailure('accepted_unverified', 'message_content_not_verified')
            part.update(status='verified', verifiedAt=timestamp())
            write_json(path, ledger)
    return {'status': 'verified', 'parts': len(payloads), 'articles': len(issue['articles'])}


def daily_targets(config):
    targets = config.get('delivery_targets')
    if (not isinstance(targets, list) or len(targets) != 2 or
            any(not isinstance(t, dict) for t in targets) or
            {t.get('role') for t in targets} != {'customer', 'internal'} or
            any(not re.fullmatch(r'oc_[A-Za-z0-9]+', t.get('chat_id', '')) for t in targets) or
            len({t['chat_id'] for t in targets}) != 2):
        raise DeliveryFailure('blocked', 'daily_targets_invalid')
    return targets


def deliver_daily(issue, config, directory, transport=request, image_keys=None):
    targets = daily_targets(config)
    # Preflight all cards before delivering the first destination.
    if issue['status'] != 'approved' or issue.get('approval', {}).get('contentHash') != issue_hash(issue):
        raise DeliveryFailure('blocked', 'issue_not_approved')
    card_payloads(issue, image_keys=image_keys)
    results = []
    for target in targets:
        try:
            result = deliver_to_group(issue, dict(config, delivery_chat_id=target['chat_id']),
                                      directory, transport, image_keys, scheduled=True)
        except DeliveryFailure as error:
            result = error.result
        except Exception as error:
            result = {'status': 'blocked', 'errorCategory': error_category(error), 'automaticRetry': False}
        results.append({'target': target['role'], **result})
    success = sum(r['status'] in ('verified', 'already_verified') for r in results)
    status = ('already_verified' if all(r['status'] == 'already_verified' for r in results) else 'verified') if success == 2 else 'partial' if success else 'failed'
    return {'status': status, 'targets': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('issue', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--test', action='store_true')
    mode.add_argument('--scheduled', action='store_true')
    parser.add_argument('--test-chat-id', help='One-off, explicitly selected test group; does not change scheduled destination')
    parser.add_argument('--dry-run', action='store_true', help='Validate targets, image fingerprints and cards; never contact Feishu')
    parser.add_argument('--image-smoke', type=Path, help='Send a clearly labeled synthetic image/text test; not a daily issue')
    args = parser.parse_args()
    try:
        issue = read_issue(args.issue)
        if issue['date'] != datetime.now(CHINA).date().isoformat():
            raise DeliveryFailure('blocked', 'issue_is_not_today')
        config = json.loads((PRIVATE / 'feishu-app.json').read_text())
        if args.test_chat_id:
            if not args.test or not re.fullmatch(r'oc_[A-Za-z0-9]+', args.test_chat_id):
                raise DeliveryFailure('blocked', 'invalid_one_off_test_target')
            config = dict(config, test_chat_id=args.test_chat_id)
        if issue['status'] != 'approved' or issue.get('approval', {}).get('contentHash') != issue_hash(issue):
            raise DeliveryFailure('blocked', 'issue_not_approved')
        if args.scheduled:
            daily_targets(config)
        workflow = json.loads((Path(__file__).resolve().parent / 'config/workflow.json').read_text())
        if args.scheduled and workflow.get('deliveryRuntime') != 'local_app':
            raise DeliveryFailure('blocked', 'delivery_runtime_not_local_app')
        if args.scheduled and not args.dry_run and not scheduled_time_allowed(datetime.now(CHINA), workflow):
            raise DeliveryFailure('blocked', 'outside_delivery_window')
        if args.scheduled and args.image_smoke:
            raise DeliveryFailure('blocked', 'synthetic_test_not_allowed_in_schedule')
        image_keys = None
        if args.image_smoke:
            # Separate, synthetic content never replaces or exports the approved daily issue.
            image_digest = hashlib.sha256(args.image_smoke.read_bytes()).hexdigest()
            source = 'https://open.feishu.cn/document/server-docs/im-v1/image/create'
            issue = {
                'schemaVersion': 1, 'id': 'daily-ai-' + issue['date'], 'date': issue['date'],
                'timezone': 'Asia/Shanghai', 'status': 'approved',
                'overview': {'paragraphs': ['这是一条图文推送测试，不是正式日报，也不是明天的新闻。用于验证本地配图上传、图文卡片发送，以及发送后的自动查询核对。']},
                'articles': [{'id': 'image-delivery-test', 'category': 'use',
                    'title': '配图测试｜图片与文字在同一张卡片里', 'author': '系统测试',
                    'publishedAt': issue['date'] + 'T00:00:00+08:00',
                    'paragraphs': ['上方是一张 1200 × 675 像素的 PNG 测试图，由本机生成并上传到飞书，不需要打开外部图片网站。',
                                   '正式日报将沿用“今日总结 → 新闻标题 → 配图 → 发生了什么与为什么值得关注 → 原文链接”的顺序。此图仅验证传输和显示，不代表任何新闻事件。'],
                    'sources': [{'label': '图片接口说明', 'url': source}],
                    'image': {'src': 'assets/' + args.image_smoke.name,
                              'width': 1200, 'height': 675, 'sha256': image_digest,
                              'alt': '配图推送测试占位图'}}]}
            issue['approval'] = {'contentHash': issue_hash(issue), 'method': 'user_requested_test'}
            key = 'img_dry_run' if args.dry_run else upload_image(args.image_smoke, config, PRIVATE / 'app-test-images')
            image_keys = {'image-delivery-test': key}
        elif any(a.get('image') for a in issue['articles']):
            image_keys = {}
            from newsletter import ROOT
            for article in issue['articles']:
                if article.get('image'):
                    image = article['image']
                    image_path = ROOT / image['src']
                    if not image.get('sha256') or hashlib.sha256(image_path.read_bytes()).hexdigest() != image['sha256']:
                        raise DeliveryFailure('blocked', 'image_fingerprint_mismatch')
                    image_keys[article['id']] = 'img_dry_run' if args.dry_run else upload_image(image_path, config, PRIVATE / 'app-images')
        if args.dry_run:
            cards = card_payloads(issue, '' if args.scheduled else '【测试】', image_keys=image_keys)
            result = {'status': 'ready', 'targets': len(daily_targets(config)) if args.scheduled else 1,
                      'partsPerTarget': len(cards), 'images': len(image_keys or {}), 'visibleTrackingIds': False}
        elif args.scheduled:
            result = deliver_daily(issue, config, PRIVATE / 'app-deliveries', image_keys=image_keys)
        else:
            result = deliver_to_group(issue, config, PRIVATE / 'app-test-deliveries', image_keys=image_keys)
    except DeliveryFailure as error:
        result = error.result
    except Exception:
        result = {'status': 'blocked', 'errorCategory': 'local_config_or_issue_error'}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] in ('verified', 'already_verified', 'ready') else 1)
