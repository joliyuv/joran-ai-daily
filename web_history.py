"""Public rolling editions and archive-first local retention. No delivery changes."""
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil

DAYS = 30


def cutoff(day):
    return (date.fromisoformat(day) - timedelta(days=DAYS - 1)).isoformat()


def archive_issues(current, root=None):
    from newsletter import ROOT, read_issue, issue_hash
    root = Path(root or ROOT)
    editions = {current['date']: current}
    for path in sorted((root / 'issues').glob('????-??-??.json')):
        if not cutoff(current['date']) <= path.stem < current['date']:
            continue
        item = read_issue(path)
        if item['date'] != path.stem:
            raise ValueError('history_date_mismatch')
        if item['status'] != 'approved' or item.get('approval', {}).get('contentHash') != issue_hash(item):
            continue
        for article in item['articles']:
            image = article.get('image')
            if image and hashlib.sha256((root / image['src']).read_bytes()).hexdigest() != image.get('sha256'):
                raise ValueError('history_image_mismatch')
        editions[item['date']] = item
    return [editions[key] for key in sorted(editions, reverse=True)]


def history_index(current, editions):
    return {'schemaVersion': 1, 'latestDate': current['date'], 'startDate': cutoff(current['date']),
            'retentionDays': DAYS, 'issues': [
                {'date': e['date'], 'count': len(e['articles']),
                 'titles': [a['title'] for a in e['articles']][:3]} for e in editions]}


def retain_local(current, root=None, vault=None, apply=False):
    """Dry-run by default. Before deletion preserve full text and images in Obsidian."""
    from newsletter import ROOT, read_issue, markdown, obsidian_note, public_issue, write_json, write_atomic, update_vault_index
    root = Path(root or ROOT).resolve()
    if vault is None:
        config = json.loads((root / 'config/workflow.json').read_text())
        vault = (root / config['obsidianDirectory']).resolve()
    vault = Path(vault).resolve()
    boundary = cutoff(current['date'])
    expired, keep_images = [], set()
    for path in sorted((root / 'issues').glob('????-??-??.json')):
        if path.is_symlink(): raise ValueError('retention_symlink')
        item = read_issue(path)
        if item['date'] != path.stem: raise ValueError('retention_date_mismatch')
        if path.stem < boundary:
            expired.append((path, item))
        else:
            keep_images.update(a['image']['src'] for a in item['articles'] if a.get('image'))
    files, directories, assets, archives = [], [], [], []
    for path, item in expired:
        text = markdown(item)
        note = vault / (item['date'] + ' AI 日报.md')
        if note.is_symlink(): raise ValueError('retention_symlink')
        if note.exists() and text not in note.read_text():
            raise ValueError('retention_archive_differs')
        copies = []
        for article in item['articles']:
            if not article.get('image'): continue
            image = article['image']; src = root / image['src']
            if src.is_symlink() or not src.is_file(): raise ValueError('retention_image_missing')
            raw = src.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
            if image.get('sha256') and image['sha256'] != digest: raise ValueError('retention_image_mismatch')
            dest = vault / '附件' / (item['date'] + '-' + src.name)
            if dest.is_symlink() or dest.parent.is_symlink(): raise ValueError('retention_symlink')
            copies.append((src, dest, digest))
        archives.append((note, item, copies))
        files.append(path)
    archived_days = {item['date'] for _, item in expired}
    for path in (root / 'inbox').iterdir():
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', path.name) and path.name < boundary:
            if path.is_symlink() or path.resolve().parent != root / 'inbox': raise ValueError('retention_symlink')
            if path.name not in archived_days and not (vault / (path.name + ' AI 日报.md')).is_file():
                raise ValueError('retention_archive_missing')
            directories.append(path)
    for path in (root / 'output').iterdir():
        if re.match(r'^\d{4}-\d{2}-\d{2}[.]', path.name) and path.name[:10] < boundary and path.is_file():
            if path.is_symlink(): raise ValueError('retention_symlink')
            if path.name[:10] not in archived_days and not (vault / (path.name[:10] + ' AI 日报.md')).is_file():
                raise ValueError('retention_archive_missing')
            files.append(path)
    expired_assets = {a['image']['src'] for _, item in expired for a in item['articles'] if a.get('image')}
    for path in (root / 'assets').iterdir():
        relative = 'assets/' + path.name
        if relative not in keep_images and (relative in expired_assets or re.fullmatch(r'news-[a-f0-9]{24}\.jpg', path.name)):
            if path.is_symlink(): raise ValueError('retention_symlink')
            assets.append(path)
    result = {'status': 'planned', 'cutoff': boundary, 'expiredIssues': len(expired),
              'files': len(files), 'inboxDirectories': len(directories), 'unusedImages': len(assets)}
    if not apply: return result
    # Finish and verify EVERY archive before removing ANY local original.
    for note, item, copies in archives:
        if not note.exists():
            body = obsidian_note(item)
            if item['status'] != 'approved': body = body.replace('状态: 已确认', '状态: 历史预览（未正式发布）')
            write_atomic(note, body)
        embeds = []
        for src, dest, digest in copies:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists(): shutil.copyfile(src, dest)
            if hashlib.sha256(dest.read_bytes()).hexdigest() != digest: raise ValueError('retention_archive_image_mismatch')
            embeds.append('![[' + '附件/' + dest.name + ']]')
        marker = '<!-- daily-ai:archived-images -->'
        content = note.read_text()
        if embeds and marker not in content:
            write_atomic(note, content + '\n\n' + marker + '\n## 本期配图\n\n' + '\n\n'.join(embeds) + '\n')
        if markdown(item) not in note.read_text(): raise ValueError('retention_archive_verification_failed')
    update_vault_index(vault)
    for path in files + assets: path.unlink()
    for path in directories: shutil.rmtree(path)
    # Keep just a current public preview, so local preview never points at deleted images.
    write_json(root / 'data/web-preview.json', public_issue(current))
    result['status'] = 'cleaned'
    write_json(root / 'output' / (current['date'] + '.retention.json'), result)
    return result
