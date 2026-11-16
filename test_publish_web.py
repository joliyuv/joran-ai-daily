from newsletter_test_fixture import fixture as synthetic_issue
"""Publication checks are offline; no real Cloudflare or Feishu calls."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

import newsletter as n
import publish_web as p


class PublicationTests(unittest.TestCase):
    def fixture(self):
        issue = synthetic_issue()
        issue['date'] = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        issue['id'] = 'daily-ai-' + issue['date']
        issue['approval']['contentHash'] = n.issue_hash(issue)
        return issue

    def test_public_bundle_excludes_internal_metadata_and_nonpublic_files(self):
        issue = self.fixture()
        issue['approvalNote'] = 'INTERNAL_REVIEW_ONLY'
        issue['articles'][0]['internalNotes'] = 'INTERNAL_EDITOR_ONLY'
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'site'
            n.build_web(issue, target, history=[issue])
            data = (target / 'data/web-preview.json').read_text()
            self.assertNotIn('INTERNAL_', data)
            self.assertNotIn('approval', data)
            self.assertNotIn('status', json.loads(data))
            self.assertEqual({f.name for f in target.iterdir()},
                {'index.html','app.js','common.js','styles.css','_headers','_redirects','data'})

    def test_modified_or_unapproved_issue_cannot_publish(self):
        for mutation in [lambda d: d.update(status='draft'),
                         lambda d: d['articles'][0]['paragraphs'].append('changed')]:
            issue = self.fixture()
            mutation(issue)
            with tempfile.TemporaryDirectory() as temporary:
                file = Path(temporary) / 'issue.json'
                n.write_json(file, issue)
                with self.assertRaisesRegex(ValueError, 'issue_not_approved_or_changed'):
                    p.publication_issue(file)

    def test_old_issue_cannot_replace_today(self):
        issue = self.fixture()
        issue['date'] = '2000-01-01'
        issue['id'] = 'daily-ai-2000-01-01'
        for article in issue['articles']: article['publishedAt'] = '2000-01-01'
        issue['approval']['contentHash'] = n.issue_hash(issue)
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'issue.json'
            n.write_json(file, issue)
            with self.assertRaisesRegex(ValueError, 'only_today_can_be_published'):
                p.publication_issue(file)

    def test_online_verification_checks_both_body_and_image_bytes(self):
        issue = self.fixture()
        image_bytes = b'fake fixture, never published'
        issue['articles'][0]['image'] = {'src':'assets/fixture.jpg', 'width':800, 'height':400,
            'alt':'test', 'sha256':hashlib.sha256(image_bytes).hexdigest()}
        def fetch(url):
            return json.dumps(n.public_issue(issue)).encode() if '/data/' in url else image_bytes
        p.verify(issue, 'https://test.pages.dev', fetch)
        with self.assertRaisesRegex(ValueError, 'published_image_mismatch'):
            p.verify(issue, 'https://test.pages.dev', lambda url: fetch(url) if '/data/' in url else b'bad')
        wrong = copy.deepcopy(n.public_issue(issue))
        wrong['overview']['paragraphs'] = ['wrong issue']
        with self.assertRaisesRegex(ValueError, 'published_content_mismatch'):
            p.verify(issue, 'https://test.pages.dev', lambda _: json.dumps(wrong).encode())


    def test_history_verification_detects_changed_historical_body(self):
        from web_history import history_index
        issue=self.fixture()
        historical=copy.deepcopy(issue)
        historical['date']='2026-11-01'
        historical['id']='daily-ai-2026-11-01'
        for a in historical['articles']: a['publishedAt']='2026-11-01'
        historical['approval']['contentHash']=n.issue_hash(historical)
        editions=[issue,historical]
        def fetch(url):
            if '/data/history.json' in url: return json.dumps(history_index(issue,editions)).encode()
            if '/data/issues/2026-11-01.json' in url: return json.dumps(n.public_issue(historical)).encode()
            if '/data/' in url: return json.dumps(n.public_issue(issue)).encode()
            name=url.split('/')[-1].split('?')[0] or 'index.html'
            return (n.ROOT/name).read_bytes()
        p.verify(issue,'https://test.pages.dev',fetch,editions)
        def changed(url):
            return b'{}' if '/data/issues/2026-11-01.json' in url else fetch(url)
        with self.assertRaisesRegex(ValueError,'published_history_mismatch'):
            p.verify(issue,'https://test.pages.dev',changed,editions)


if __name__ == '__main__':
    unittest.main()
