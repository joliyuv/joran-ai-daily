from newsletter_test_fixture import fixture as synthetic_issue
"""Synthetic images and fake source transport; no real downloads or publication."""
import copy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import newsletter_images as images
from newsletter import ROOT, read_issue


class ImagePreparationTests(unittest.TestCase):
    def setUp(self):
        self.issue = copy.deepcopy(synthetic_issue())
        self.issue['status'] = 'draft'
        self.issue.pop('approval', None)
        self.issue['articles'] = self.issue['articles'][:1]
        out = BytesIO()
        Image.new('RGB', (1200, 675), '#507080').save(out, format='PNG')
        self.raw = out.getvalue()

    def test_source_image_is_attached_with_provenance_and_fingerprint(self):
        def fetch(url, limit):
            if url.endswith('sample.png'): return self.raw, url, 'image/png'
            return b'<meta property="og:image" content="/sample.png">', url, 'text/html'
        with tempfile.TemporaryDirectory() as folder, patch.object(images, 'ROOT', Path(folder)):
            path = Path(folder) / 'issue.json';path.write_text(json.dumps(self.issue))
            result = images.prepare(path, fetch)
            self.assertEqual(result['images'], 1)
            image = json.loads(path.read_text())['articles'][0]['image']
            self.assertTrue((Path(folder) / image['src']).exists())
            self.assertEqual(image['sourceUrl'], self.issue['articles'][0]['sources'][0]['url'])
            self.assertEqual(len(image['sha256']), 64)

    def test_failed_image_does_not_create_placeholder(self):
        def fetch(*args): raise TimeoutError()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'issue.json';path.write_text(json.dumps(self.issue))
            self.assertEqual(images.prepare(path, fetch)['images'], 0)
            self.assertNotIn('image', json.loads(path.read_text())['articles'][0])

    def test_approved_issue_is_untouched(self):
        self.issue['status'] = 'approved'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'issue.json';path.write_text(json.dumps(self.issue))
            before = path.read_bytes()
            self.assertEqual(images.prepare(path)['status'], 'kept_approved')
            self.assertEqual(before, path.read_bytes())

    def test_small_icons_are_rejected(self):
        raw = BytesIO();Image.new('RGB', (64, 64)).save(raw, format='PNG')
        with self.assertRaises(ValueError): images.normalize_image(raw.getvalue())

    def test_proxy_dns_requires_verified_public_dns_answer(self):
        addr = [(2, 1, 6, '', ('198.18.0.7', 443))]
        response = BytesIO(json.dumps({'Status': 0, 'Answer': [{'type': 1, 'data': '93.184.216.34'}]}).encode())
        with patch.object(images.socket, 'getaddrinfo', return_value=addr), patch.object(images, 'urlopen', return_value=response):
            self.assertEqual(images.safe_url('https://public.example/image'), 'https://public.example/image')

    def test_proxy_dns_cannot_authorize_private_real_destination(self):
        addr = [(2, 1, 6, '', ('198.18.0.7', 443))]
        response = BytesIO(json.dumps({'Status': 0, 'Answer': [{'type': 1, 'data': '127.0.0.1'}]}).encode())
        with patch.object(images.socket, 'getaddrinfo', return_value=addr), patch.object(images, 'urlopen', return_value=response):
            with self.assertRaises(ValueError): images.safe_url('https://public.example/image')

    def test_private_network_is_still_rejected_without_dns_fallback(self):
        addr = [(2, 1, 6, '', ('10.0.0.2', 443))]
        with patch.object(images.socket, 'getaddrinfo', return_value=addr), patch.object(images, 'urlopen') as dns:
            with self.assertRaises(ValueError): images.safe_url('https://private.example/image')
            dns.assert_not_called()


if __name__ == '__main__': unittest.main()
