"""Publish today's approved issue as static files to the existing free Pages project."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from newsletter import ROOT, build_web, issue_hash, public_issue, read_issue, write_json
from web_history import archive_issues, history_index, retain_local


def publication_issue(path):
    issue = read_issue(path)
    if issue['status'] != 'approved' or issue.get('approval', {}).get('contentHash') != issue_hash(issue):
        raise ValueError('issue_not_approved_or_changed')
    if issue['date'] != datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat():
        raise ValueError('only_today_can_be_published')
    for article in issue['articles']:
        if article.get('image'):
            image = article['image']
            if hashlib.sha256((ROOT / image['src']).read_bytes()).hexdigest() != image.get('sha256'):
                raise ValueError('image_fingerprint_mismatch')
    return issue


def get(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'Cache-Control': 'no-cache',
                         'User-Agent': 'Mozilla/5.0 JORAN-newsletter/1.0'}), timeout=20) as response:
                return response.read()
        except OSError:
            if attempt == 2: raise
            time.sleep(2 * (attempt + 1))


def verify(issue, url, fetch=get, history=None):
    fingerprint = issue_hash(issue)
    remote = json.loads(fetch(url + '/data/web-preview.json?v=' + fingerprint))
    if remote != public_issue(issue):
        raise ValueError('published_content_mismatch')
    for article in issue['articles']:
        if article.get('image'):
            image = article['image']
            raw = fetch(url + '/' + image['src'])
            if hashlib.sha256(raw).hexdigest() != image['sha256']:
                raise ValueError('published_image_mismatch')

    if history is not None:
        from concurrent.futures import ThreadPoolExecutor
        index = json.loads(fetch(url + '/data/history.json?v=' + fingerprint))
        if index != history_index(issue, history):
            raise ValueError('published_history_mismatch')
        checked = {a['image']['src'] for a in issue['articles'] if a.get('image')}
        checks = []
        for edition in history:
            checks.append(('json', url + '/data/issues/' + edition['date'] + '.json?v=' + fingerprint, public_issue(edition)))
            for article in edition['articles']:
                image = article.get('image')
                if image and image['src'] not in checked:
                    checks.append(('image', url + '/' + image['src'], image['sha256']))
                    checked.add(image['src'])
        for filename in ['index.html', 'app.js', 'styles.css', 'common.js']:
            checks.append(('interface', url + '/' + ('' if filename == 'index.html' else filename) + '?v=' + fingerprint, (ROOT / filename).read_bytes()))
        def check(task):
            kind, address, expected = task
            raw = fetch(address)
            if kind == 'json' and json.loads(raw) != expected: raise ValueError('published_history_mismatch')
            if kind == 'image' and hashlib.sha256(raw).hexdigest() != expected: raise ValueError('published_image_mismatch')
            if kind == 'interface' and raw != expected: raise ValueError('published_interface_mismatch')
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(check, checks))


def publish(path, build_only=None, verify_only=False):
    issue = publication_issue(path)
    history = archive_issues(issue)
    config = json.loads((ROOT / 'config/web.json').read_text())
    project = config['project']
    import re
    if not re.fullmatch(r'[a-z0-9-]+', project) or config['url'] != f'https://{project}.pages.dev':
        raise ValueError('invalid_pages_configuration')
    if build_only:
        build_web(issue, build_only, history)
        return {'status': 'built', 'date': issue['date'], 'directory': str(build_only)}
    # Each deployment is an isolated allowlisted bundle, never the project directory.
    if not verify_only:
        with tempfile.TemporaryDirectory(prefix='joran-daily-web-') as temporary:
            build_web(issue, temporary, history)
            command = config['wrangler'] + ['pages', 'deploy', temporary,
                '--project-name', project, '--branch', config['branch'], '--commit-dirty=true']
            env = {**os.environ, 'CLOUDFLARE_ACCOUNT_ID': config['accountId'], 'WRANGLER_SEND_METRICS': 'false', 'CI': 'true'}
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
            if result.returncode:
                diagnostic = (result.stderr + result.stdout).lower()
                if any(token in diagnostic for token in ['not logged in', 'token has expired', 'authentication error']):
                    raise ValueError('cloudflare_login_required')
                raise ValueError('cloudflare_deployment_failed')
        write_json(ROOT / 'output' / (issue['date'] + '.web.json'), {
            'status': 'uploaded_pending_verification', 'date': issue['date'],
            'contentHash': issue_hash(issue), 'url': config['url'], 'verified': False})
    verify(issue, config['url'], history=history)
    receipt = {'status': 'published', 'date': issue['date'], 'contentHash': issue_hash(issue),
               'url': config['url'], 'verified': True,
               'publishedAt': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')}
    write_json(ROOT / 'output' / (issue['date'] + '.web.json'), receipt)
    if config.get('cleanupAfterVerifiedPublication') is True:
        try:
            receipt['retention'] = retain_local(issue, apply=True)
        except (ValueError, OSError):
            receipt['retention'] = {'status': 'blocked', 'errorCategory': 'retention_archive_check_failed'}
        write_json(ROOT / 'output' / (issue['date'] + '.web.json'), receipt)
    if config.get('retireSupersededNewsDeployments') is True:
        from cloudflare_retention import retire_snapshots
        try:
            receipt['snapshotRetention'] = retire_snapshots(config, history_index(issue, history), get, ROOT)
        except (ValueError, OSError, subprocess.SubprocessError):
            receipt['snapshotRetention'] = {'status':'blocked', 'errorCategory':'cloudflare_snapshot_cleanup_failed'}
        write_json(ROOT / 'output' / (issue['date'] + '.web.json'), receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('issue', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--build-only', type=Path)
    mode.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(publish(args.issue, args.build_only, args.verify_only), ensure_ascii=False))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        known = {'issue_not_approved_or_changed', 'only_today_can_be_published', 'image_fingerprint_mismatch',
                 'published_content_mismatch', 'published_image_mismatch', 'invalid_pages_configuration',
                 'cloudflare_login_required', 'cloudflare_deployment_failed', 'published_history_mismatch', 'published_interface_mismatch', 'history_image_mismatch', 'history_date_mismatch'}
        category = str(error) if str(error) in known else 'web_publication_failed'
        print(json.dumps({'status': 'failed', 'errorCategory': category}, ensure_ascii=False))
        raise SystemExit(1)
