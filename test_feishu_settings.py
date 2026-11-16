"""Local setup checks; uses fake credentials and a temporary directory only."""
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('settings', Path(__file__).with_name('feishu-settings.py'))
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)
FAKE = 'https://open.feishu.cn/open-apis/bot/v2/hook/local-test-only'


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings.CONFIG_DIR = Path(self.temp.name) / 'private'

    def tearDown(self):
        self.temp.cleanup()

    def test_save_permissions_and_keep_secrets_on_blank_input(self):
        settings.save_config({'webhook': FAKE, 'signature_enabled': True, 'secret': 'fake-secret'})
        updated = settings.save_config({'signature_enabled': True, 'keyword': 'AI 日报'})
        self.assertEqual(updated['webhook'], FAKE)
        self.assertEqual(updated['secret'], 'fake-secret')
        self.assertEqual(stat.S_IMODE((settings.CONFIG_DIR / 'feishu.json').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(settings.CONFIG_DIR.stat().st_mode), 0o700)

    def test_only_accept_feishu_webhook(self):
        for url in ['https://evil.example/open-apis/bot/v2/hook/test', FAKE+'?token=1', FAKE+'/extra']:
            with self.assertRaises(ValueError):
                settings.save_config({'webhook': url})
        self.assertFalse(settings.CONFIG_DIR.exists())

    def test_changing_bot_cannot_reuse_previous_signing_key(self):
        settings.save_config({'webhook': FAKE, 'signature_enabled': True, 'secret': 'fake-key-a'})
        with self.assertRaises(ValueError):
            settings.save_config({'webhook': FAKE + '-new', 'signature_enabled': True})
        self.assertEqual(settings.load_config()['webhook'], FAKE)
        updated = settings.save_config({'webhook': FAKE + '-new', 'signature_enabled': True,
                                        'secret': 'fake-key-b'})
        self.assertEqual(updated['secret'], 'fake-key-b')

    def test_signature_requires_key_and_disable_clears_key(self):
        with self.assertRaises(ValueError):
            settings.save_config({'webhook': FAKE, 'signature_enabled': True})
        settings.save_config({'webhook': FAKE, 'signature_enabled': True, 'secret': 'fake-secret'})
        self.assertEqual(settings.save_config({})['secret'], '')

    def handler(self, path, headers):
        handler = settings.Handler.__new__(settings.Handler)
        handler.path, handler.headers = path, headers
        handler.respond = Mock()
        return handler

    def test_status_never_returns_credentials(self):
        settings.save_config({'webhook': FAKE, 'secret': 'fake-secret', 'signature_enabled': True})
        handler = self.handler('/status', {'Host': '127.0.0.1:8768'})
        handler.do_GET()
        response = handler.respond.call_args.args[1]
        self.assertNotIn(FAKE, response)
        self.assertNotIn('fake-secret', response)
        self.assertTrue(json.loads(response)['configured'])

    def test_cross_origin_and_wrong_token_cannot_save(self):
        for headers in [
            {'Host': 'evil.example', 'Origin': settings.ORIGIN, 'X-Setup-Token': settings.TOKEN},
            {'Host': '127.0.0.1:8768', 'Origin': 'https://evil.example', 'X-Setup-Token': settings.TOKEN},
            {'Host': '127.0.0.1:8768', 'Origin': settings.ORIGIN, 'X-Setup-Token': 'wrong'}]:
            handler = self.handler('/save', headers)
            handler.do_POST()
            self.assertEqual(handler.respond.call_args.args[0], 403)
        self.assertFalse(settings.CONFIG_DIR.exists())


if __name__ == '__main__':
    unittest.main()
