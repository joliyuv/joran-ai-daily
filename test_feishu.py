from newsletter_test_fixture import fixture as synthetic_issue
"""Delivery tests use a fake transport and temporary ledgers; never send messages."""
import base64
import copy
import hashlib
import hmac
import json
import socket
import ssl
from urllib.error import HTTPError, URLError
from pathlib import Path
import tempfile
import unittest
import feishu
from newsletter import ROOT, read_issue, issue_hash


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.issue = synthetic_issue()
        self.config = {'webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/fake-test-only'}

    def test_success_is_not_sent_twice(self):
        calls = []
        def sender(url, payload):
            calls.append(payload)
            return {'code': 0}
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(feishu.deliver(self.issue, self.config, folder, sender)['status'], 'accepted')
            self.assertEqual(feishu.deliver(self.issue, self.config, folder, sender)['status'], 'already_sent')
            self.assertEqual(len(calls), 1)
            self.assertNotIn(self.config['webhook'], ''.join(p.read_text() for p in Path(folder).glob('*.json')))

    def test_unknown_result_blocks_retry(self):
        calls = []
        def sender(url, payload):
            calls.append(payload)
            raise TimeoutError()
        with tempfile.TemporaryDirectory() as folder:
            for _ in range(2):
                with self.assertRaises(ValueError):
                    feishu.deliver(self.issue, self.config, folder, sender)
            self.assertEqual(len(calls), 1)

    def test_safe_error_categories_do_not_leak_credentials(self):
        cases = [
            (TimeoutError('fake-secret'), 'network_timeout'),
            (URLError(socket.gaierror(-2, self.config['webhook'])), 'dns_failure'),
            (URLError(PermissionError(1, 'fake-secret')), 'network_permission_denied'),
            (ssl.SSLError('fake-secret'), 'tls_failure'),
            (ConnectionRefusedError('fake-secret'), 'connection_refused'),
            (ConnectionResetError('fake-secret'), 'connection_interrupted'),
            (ValueError('fake-secret'), 'invalid_response'),
            (HTTPError(self.config['webhook'], 429, 'fake-secret', {}, None), 'http_rate_limited'),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder:
                def sender(*_):
                    raise error
                with self.assertRaises(feishu.DeliveryFailure) as caught:
                    feishu.deliver(self.issue, self.config, folder, sender)
                self.assertEqual(caught.exception.result['errorCategory'], expected)
                self.assertFalse(caught.exception.result['automaticRetry'])
                stored = ''.join(p.read_text() for p in Path(folder).glob('*.json'))
                self.assertIn(expected, stored)
                self.assertNotIn('fake-secret', stored + str(caught.exception))
                self.assertNotIn(self.config['webhook'], stored + str(caught.exception))

    def test_invalid_success_response_stays_unknown(self):
        for response in [None, {}, {'code': False}, {'code': '0'}, {'code': [0]}]:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(feishu.DeliveryFailure) as caught:
                    feishu.deliver(self.issue, self.config, folder, lambda *_: response)
                self.assertEqual(caught.exception.result['status'], 'unknown')
                self.assertEqual(caught.exception.result['errorCategory'], 'invalid_response')

    def test_rejected_response_has_safe_code_only(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(feishu.DeliveryFailure) as caught:
                feishu.deliver(self.issue, self.config, folder,
                              lambda *_: {'code': 19024, 'msg': self.config['webhook']})
            self.assertEqual(caught.exception.result['status'], 'rejected')
            stored = json.loads(next(Path(folder).glob('*.json')).read_text())
            self.assertEqual(stored['parts'][0]['code'], 19024)
            self.assertNotIn(self.config['webhook'], json.dumps(stored))

    def test_changed_approved_content_is_not_sent(self):
        self.issue['articles'][0]['title'] += 'changed'
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                feishu.deliver(self.issue, self.config, folder, lambda *_: self.fail('should not send'))

    def test_signing_and_keyword(self):
        self.config.update(signature_enabled=True, secret='fake-secret', keyword='日报推送')
        def sender(url, payload):
            key = (payload['timestamp'] + '\n' + self.config['secret']).encode()
            expected = base64.b64encode(hmac.new(key, b'', hashlib.sha256).digest()).decode()
            self.assertEqual(payload['sign'], expected)
            self.assertIn('日报推送', payload['card']['header']['title']['content'])
            return {'code': 0}
        with tempfile.TemporaryDirectory() as folder:
            feishu.deliver(self.issue, self.config, folder, sender)

    def test_long_issue_splits_before_sending(self):
        articles = self.issue['articles']
        self.issue['articles'] = []
        for index in range(30):
            for article in articles:
                item = copy.deepcopy(article)
                item['id'] += '-' + str(index)
                self.issue['articles'].append(item)
        payloads = feishu.card_payloads(self.issue)
        self.assertGreater(len(payloads), 1)
        self.assertTrue(all(len(__import__('json').dumps(p, ensure_ascii=False).encode()) < 19000 for p in payloads))

    def test_images_are_between_title_and_body(self):
        issue = synthetic_image_issue()
        keys = {a['id']: 'img_test_' + a['id'] for a in issue['articles'] if a.get('image')}
        cards = feishu.card_payloads(issue, image_keys=keys)
        elements = [e for card in cards for e in card['card']['body']['elements']]
        images = [e for e in elements if e['tag'] == 'img']
        self.assertEqual(len(images), len(keys))
        for article in issue['articles']:
            if article.get('image'):
                index = next(i for i, e in enumerate(elements) if e.get('img_key') == keys[article['id']])
                self.assertIn(article['title'], elements[index-1]['content'])
                for paragraph in article['paragraphs']:
                    self.assertIn(paragraph, elements[index+1]['content'])
                self.assertEqual(elements[index]['alt']['content'], article['image']['alt'])
        text = '\n'.join(e.get('content', '') for e in elements)
        for article in issue['articles']:
            for source in article['sources']:
                self.assertIn(source['url'], text)
        self.assertTrue(all(len(json.dumps(p, ensure_ascii=False).encode()) <= 19000 for p in cards))

    def test_missing_image_key_does_not_silently_drop_image(self):
        issue = synthetic_image_issue()
        with self.assertRaises(ValueError):
            feishu.card_payloads(issue)

    def test_large_image_story_stops_before_sending(self):
        issue = synthetic_image_issue()
        article = next(a for a in issue['articles'] if a.get('image'))
        issue['articles'] = [article]
        article['paragraphs'] = ['过长段落' * 10000]
        with self.assertRaises(ValueError):
            feishu.card_payloads(issue, image_keys={article['id']: 'img_test'})





def synthetic_image_issue():
    x=synthetic_issue()
    x['articles'][0]['image']={'src':'assets/fixture.jpg','width':800,'height':400,'alt':'模拟图片'}
    return x

if __name__ == '__main__':
    unittest.main()
