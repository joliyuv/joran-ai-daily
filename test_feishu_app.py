from newsletter_test_fixture import fixture as synthetic_issue
"""Fake transport only: verify receipt recovery and prevent duplicate sends."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from feishu_app import deliver_to_group, deliver_daily, daily_targets, DeliveryFailure, scheduled_time_allowed
from newsletter import ROOT, read_issue, issue_hash


class ApplicationDeliveryTests(unittest.TestCase):
    def test_same_day_catchup_is_opt_in_and_not_before_ten(self):
        at = lambda hour, minute: datetime(2026, 10, 21, hour, minute)
        self.assertFalse(scheduled_time_allowed(at(10, 36), {}))
        self.assertTrue(scheduled_time_allowed(at(10, 10), {}))
        self.assertFalse(scheduled_time_allowed(at(10, 11), {}))
        policy = {'allowSameDayCatchUp': True}
        self.assertFalse(scheduled_time_allowed(at(9, 59), policy))
        self.assertTrue(scheduled_time_allowed(at(10, 0), policy))
        self.assertTrue(scheduled_time_allowed(at(23, 59), policy))

    def setUp(self):
        self.issue = synthetic_issue()
        self.config = {'app_id': 'cli_fake', 'app_secret': 'not-a-secret', 'test_chat_id': 'oc_fake'}
        self.sent = []
        self.fail_query = False
        self.wrong_chat = False
        self.corrupt_content = False
        self.remove_images = False

    def transport(self, method, path, data=None, token=None):
        if 'tenant_access_token' in path:
            return {'code': 0, 'tenant_access_token': 'fake-token'}
        if method == 'POST':
            self.sent.append(copy.deepcopy(data))
            return {'code': 0, 'data': {'message_id': 'om_fake'}}
        if self.fail_query:
            raise DeliveryFailure('unknown', 'network_timeout')
        card = json.loads(self.sent[-1]['content'])
        for element in card['body']['elements']:
            if element['tag'] == 'markdown':
                element['content'] = element['content'].replace('\n---\n', '\n<hr>\n').replace('\n', '\\n')
        if self.corrupt_content:
            card['body']['elements'][0]['content'] = '正文不完整'
        if self.remove_images:
            card['body']['elements'] = [e for e in card['body']['elements'] if e['tag'] != 'img']
        return {'code': 0, 'data': {'items': [{
            'message_id': 'om_fake', 'chat_id': 'oc_wrong' if self.wrong_chat else 'oc_fake',
            'deleted': False, 'sender': {'id': 'cli_fake', 'sender_type': 'app'},
            'body': {'content': json.dumps(card, ensure_ascii=False)}}]}}

    def test_verified_send_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(deliver_to_group(self.issue, self.config, folder, self.transport)['status'], 'verified')
            self.assertEqual(deliver_to_group(self.issue, self.config, folder, self.transport)['status'], 'already_verified')
            self.assertEqual(len(self.sent), 1)
            ledger = ''.join(p.read_text() for p in Path(folder).glob('*.json'))
            self.assertNotIn('fake-token', ledger)
            self.assertNotIn('not-a-secret', ledger)

    def test_query_failure_resumes_query_without_resending(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fail_query = True
            with self.assertRaises(DeliveryFailure): deliver_to_group(self.issue, self.config, folder, self.transport)
            self.fail_query = False
            self.assertEqual(deliver_to_group(self.issue, self.config, folder, self.transport)['status'], 'verified')
            self.assertEqual(len(self.sent), 1)

    def test_wrong_destination_is_not_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            self.wrong_chat = True
            with self.assertRaises(DeliveryFailure) as error: deliver_to_group(self.issue, self.config, folder, self.transport)
            self.assertEqual(error.exception.result['status'], 'accepted_unverified')

    def test_incomplete_content_is_not_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            self.corrupt_content = True
            with self.assertRaises(DeliveryFailure): deliver_to_group(self.issue, self.config, folder, self.transport)

    def test_image_key_is_required_in_queried_card(self):
        self.issue['articles'][0]['image'] = {'src': 'assets/test.png', 'width': 1200, 'height': 675, 'alt': 'test'}
        self.issue['approval']['contentHash'] = issue_hash(self.issue)
        keys = {self.issue['articles'][0]['id']: 'img_fake_key'}
        with tempfile.TemporaryDirectory() as folder:
            self.remove_images = True
            with self.assertRaises(DeliveryFailure):
                deliver_to_group(self.issue, self.config, folder, self.transport, keys)
            self.remove_images = False
            self.assertEqual(deliver_to_group(self.issue, self.config, folder, self.transport, keys)['status'], 'verified')
            self.assertEqual(len(self.sent), 1)

    def test_unknown_send_is_never_blindly_retried(self):
        calls = []
        def timeout(method, path, data=None, token=None):
            if method == 'POST' and 'im/v1/messages' in path:
                calls.append(data)
                raise DeliveryFailure('unknown', 'network_timeout')
            return self.transport(method, path, data, token)
        with tempfile.TemporaryDirectory() as folder:
            for _ in range(2):
                with self.assertRaises(DeliveryFailure): deliver_to_group(self.issue, self.config, folder, timeout)
            self.assertEqual(len(calls), 1)

    def test_explicit_api_rejection_can_recover_on_next_run(self):
        attempts = []
        def reject_once(method, path, data=None, token=None):
            if method == 'POST' and 'im/v1/messages' in path:
                attempts.append(data)
                if len(attempts) == 1:
                    raise DeliveryFailure('rejected', 'feishu_api_99991400')
            return self.transport(method, path, data, token)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(DeliveryFailure):
                deliver_to_group(self.issue, self.config, folder, reject_once)
            self.assertEqual(deliver_to_group(self.issue, self.config, folder, reject_once)['status'], 'verified')
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0]['uuid'], attempts[1]['uuid'])
            self.assertEqual(len(self.sent), 1)

    def test_daily_sends_identical_clean_content_to_both_groups_once(self):
        config = dict(self.config, delivery_targets=[
            {'role': 'customer', 'chat_id': 'oc_customer'},
            {'role': 'internal', 'chat_id': 'oc_internal'}])
        sent = {}
        def transport(method, path, data=None, token=None):
            if 'tenant_access_token' in path: return {'tenant_access_token': 'fake-token'}
            if method == 'POST':
                mid = 'om_' + str(len(sent) + 1)
                sent[mid] = copy.deepcopy(data)
                return {'data': {'message_id': mid}}
            mid = path.split('/')[3].split('?')[0]
            message = sent[mid]
            return {'data': {'items': [{'message_id': mid, 'chat_id': message['receive_id'], 'deleted': False,
                'sender': {'id': 'cli_fake', 'sender_type': 'app'}, 'body': {'content': message['content']}}]}}
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(deliver_daily(self.issue, config, folder, transport)['status'], 'verified')
            self.assertEqual(deliver_daily(self.issue, config, folder, transport)['status'], 'already_verified')
            self.assertEqual(len(sent), 2)
            self.assertEqual(sent['om_1']['content'], sent['om_2']['content'])
            self.assertNotIn('JORAN-', sent['om_1']['content'])
            self.assertNotIn('【测试】', sent['om_1']['content'])
            self.assertNotEqual(sent['om_1']['uuid'], sent['om_2']['uuid'])

    def test_customer_failure_does_not_block_internal_or_repeat_success(self):
        config = dict(self.config, delivery_targets=[
            {'role': 'customer', 'chat_id': 'oc_customer'},
            {'role': 'internal', 'chat_id': 'oc_fake'}])
        customer_calls = []
        def transport(method, path, data=None, token=None):
            if data and data.get('receive_id') == 'oc_customer':
                customer_calls.append(data)
                raise DeliveryFailure('unknown', 'network_timeout')
            return self.transport(method, path, data, token)
        with tempfile.TemporaryDirectory() as folder:
            result = deliver_daily(self.issue, config, folder, transport)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['targets'][1]['status'], 'verified')
            result = deliver_daily(self.issue, config, folder, transport)
            self.assertEqual(result['targets'][1]['status'], 'already_verified')
            self.assertEqual(len(customer_calls), 1)
            self.assertEqual(len(self.sent), 1)

    def test_duplicate_daily_target_is_blocked_before_sending(self):
        config = dict(self.config, delivery_targets=[
            {'role': 'customer', 'chat_id': 'oc_fake'},
            {'role': 'internal', 'chat_id': 'oc_fake'}])
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(DeliveryFailure): deliver_daily(self.issue, config, folder, self.transport)
            self.assertEqual(self.sent, [])


if __name__ == '__main__': unittest.main()
