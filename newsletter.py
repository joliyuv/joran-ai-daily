"""One issue, multiple outputs. No message sending, scheduler, or website sync."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os
import uuid
from sources import collect, date_of, public_url, timestamp
from datetime import date

ROOT = Path(__file__).resolve().parent
CATEGORIES = {'code': '编程与技术', 'art': '艺术与创作', 'design': '设计与产品',
              'use': '应用与效率', 'business': '商业与产业', 'science': '科学与研究',
              'human': '人文与社会', 'policy': '政策与治理'}


def write_atomic(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(body)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, data):
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def paragraphs(value):
    return isinstance(value, list) and bool(value) and all(isinstance(p, str) and p.strip() for p in value)


def validate(issue):
    if issue.get('schemaVersion') != 1:
        raise ValueError('不支持的日报格式版本')
    issue_date = date.fromisoformat(issue['date'])
    if issue.get('id') != 'daily-ai-' + issue['date'] or issue.get('timezone') != 'Asia/Shanghai':
        raise ValueError('期号或时区不正确')
    if issue.get('status') not in {'draft', 'approved', 'preview'}:
        raise ValueError('日报状态不正确')
    if not paragraphs(issue.get('overview', {}).get('paragraphs')):
        raise ValueError('日报需要今日总结')
    if not isinstance(issue.get('articles'), list) or not issue['articles']:
        raise ValueError('日报不能没有新闻')
    ids = set()
    for article in issue['articles']:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', article.get('id', '')) or article['id'] in ids:
            raise ValueError('新闻 ID 无效或重复')
        ids.add(article['id'])
        if (article.get('category') not in CATEGORIES or
                not isinstance(article.get('title'), str) or not article['title'].strip()):
            raise ValueError('新闻缺少标题或分类')
        if not isinstance(article.get('author'), str) or not article['author'].strip():
            raise ValueError('新闻缺少作者或发布方')
        if not paragraphs(article.get('paragraphs')):
            raise ValueError('新闻需要完整段落正文')
        if date_of(article['publishedAt']) > issue_date:
            raise ValueError('新闻发布时间不能晚于本期日期')
        if not article.get('sources'):
            raise ValueError('每条新闻都需要原文链接')
        for source in article['sources']:
            public_url(source['url'])
            if not isinstance(source.get('label'), str) or not source['label'].strip():
                raise ValueError('来源链接缺少名称')
        if article.get('image'):
            image = article['image']
            relative = Path(image['src'])
            if (relative.parent != Path('assets') or relative.name.startswith('.') or
                    relative.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp', '.svg'}):
                raise ValueError('配图必须使用 assets 下的本地图片')
            if any(type(image.get(key)) is not int or image[key] <= 0 for key in ['width', 'height']):
                raise ValueError('配图尺寸必须是正整数')
            if not isinstance(image.get('alt'), str):
                raise ValueError('配图缺少说明')
    return issue


def read_issue(path):
    return validate(json.loads(Path(path).read_text()))


def issue_hash(issue):
    content = {key: issue[key] for key in
               ['schemaVersion', 'id', 'date', 'timezone', 'overview', 'articles']}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def markdown(issue):
    validate(issue)
    body = ['**每日 AI｜' + issue['date'].replace('-', ' / ') + '**',
            f"本期精选 {len(issue['articles'])} 则 · 含近期值得继续关注的动态", '', '**AI 今日总结**', '']
    body += [p + '\n' for p in issue['overview']['paragraphs']]
    for index, article in enumerate(issue['articles'], 1):
        body += ['---', '', f"**{index:02d}｜{CATEGORIES[article['category']]}**",
                 '**' + article['title'] + '**', '']
        body += [p + '\n' for p in article['paragraphs']]
        body += [article.get('author', '') + ' · ' + article['publishedAt'][:10],
                 ' · '.join(f"[{s['label']}]({s['url']})" for s in article['sources']), '']
    return '\n'.join(body + ['---', '', '*JORAN · AI 整理，保留原文出处*', ''])


def obsidian_note(issue):
    title = f"{issue['date']} AI 日报"
    state = '自动整理' if issue.get('approval', {}).get('method') == 'scheduled_policy' else '已确认'
    header = f'''---
类型: AI日报
日期: {issue['date']}
状态: {state}
期号: {issue['id']}
内容指纹: {issue_hash(issue)}
来源: AI 整理
tags:
  - AI日报
---

# {title}

'''
    return header + markdown(issue)


def update_vault_index(vault):
    path = Path(vault) / '00 AI 日报.md'
    start, end = '<!-- daily-ai:start -->', '<!-- daily-ai:end -->'
    links = []
    for note in sorted(Path(vault).glob('????-??-?? AI 日报.md'), reverse=True):
        links.append(f'- [[{note.stem}|{note.stem[:10]} · 每日 AI]]')
    block = start + '\n' + '\n'.join(links) + '\n' + end
    if path.exists():
        text = path.read_text()
        if start not in text or end not in text:
            return  # Never replace an existing manually maintained index.
        text = text.split(start)[0] + block + text.split(end, 1)[1]
    else:
        text = '# AI 日报\n\n按日期阅读已确认的日报与原文出处。\n\n' + block + '\n'
    write_atomic(path, text)


def export_issue(issue, output, vault=None):
    validate(issue)
    if issue['status'] != 'approved':
        raise ValueError('只有已确认的一期才可正式归档；草稿可以用 preview 命令查看')
    if issue.get('approval', {}).get('contentHash') != issue_hash(issue):
        raise ValueError('正文在确认后有变化，请重新审阅确认后归档')
    output = Path(output)
    if vault:
        target = Path(vault) / (issue['date'] + ' AI 日报.md')
        content = obsidian_note(issue)
        if target.exists() and target.read_text() != content:
            raise ValueError('Obsidian 已有不同内容，未覆盖；请先检查人工修改或另存版本')
    write_atomic(output / (issue['date'] + '.md'), markdown(issue))
    if vault:
        if not target.exists():
            write_atomic(target, content)
        update_vault_index(vault)
    write_json(output / (issue['date'] + '.export.json'), {
        'issueId': issue['id'], 'contentHash': issue_hash(issue), 'exportedAt': timestamp(),
        'obsidian': bool(vault), 'feishu': 'handled_by_delivery_ledger', 'web': 'handled_by_web_publication_receipt'})


def build_web(issue, output, history=None):
    """Build only public content, with a rolling archive and shared image assets."""
    import shutil
    from web_history import archive_issues, history_index
    validate(issue)
    history = archive_issues(issue) if history is None else history
    output = Path(output)
    if output.resolve() == ROOT.resolve() or ROOT.resolve() in output.resolve().parents:
        raise ValueError('网页预览请输出到项目目录之外，避免混入配置工具')
    if output.exists() and any(output.iterdir()):
        raise ValueError('请使用空的输出目录，避免混入旧文件')
    output.mkdir(parents=True, exist_ok=True)
    for filename in ['index.html', 'app.js', 'common.js', 'styles.css', '_redirects', '_headers']:
        shutil.copyfile(ROOT / filename, output / filename)
    write_json(output / 'data/web-preview.json', public_issue(issue))
    write_json(output / 'data/history.json', history_index(issue, history))
    for edition in history:
        write_json(output / 'data/issues' / (edition['date'] + '.json'), public_issue(edition))
        for article in edition['articles']:
            if article.get('image'):
                relative = Path(article['image']['src'])
                if relative.parent != Path('assets') or relative.name.startswith('.'):
                    raise ValueError('配图路径必须位于 assets 目录')
                (output / 'assets').mkdir(exist_ok=True)
                shutil.copyfile(ROOT / relative, output / relative)


def public_issue(issue):
    """Only reader-facing content leaves the local publication workflow."""
    public = {key: issue[key] for key in ['schemaVersion', 'id', 'date', 'timezone', 'overview']}
    public['articles'] = []
    for article in issue['articles']:
        item = {key: article[key] for key in
                ['id', 'category', 'title', 'author', 'publishedAt', 'paragraphs', 'sources']}
        for key in ['kind', 'observedAt']:
            if key in article: item[key] = article[key]
        if article.get('image'):
            item['image'] = {key: article['image'][key] for key in
                             ['src', 'width', 'height', 'alt', 'caption'] if key in article['image']}
        public['articles'].append(item)
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['validate', 'preview', 'approve', 'export', 'build-web']:
        command = commands.add_parser(name)
        command.add_argument('issue', type=Path)
        if name in {'export', 'build-web'}:
            command.add_argument('--output', type=Path, default=ROOT / 'output' if name == 'export' else None,
                                 required=name == 'build-web')
        if name == 'export':
            command.add_argument('--obsidian', action='store_true')
        if name == 'approve':
            review = command.add_mutually_exclusive_group(required=True)
            review.add_argument('--reviewed', action='store_true', help='仅在用户已确认此版本后使用')
            review.add_argument('--scheduled', action='store_true', help='依据用户授权的每日自动出版规则')
    command = commands.add_parser('collect')
    command.add_argument('--date', required=True)
    command.add_argument('--sources', type=Path, default=ROOT / 'config/sources.json')
    args = parser.parse_args()
    if args.command == 'collect':
        bundle = collect(json.loads(args.sources.read_text()), args.date)
        batch = ROOT / 'inbox' / args.date / ('collection-' + uuid.uuid4().hex[:12] + '.json')
        write_json(batch, bundle)
        print(json.dumps({'file': str(batch), 'items': len(bundle['items']),
                          'sources': bundle['sources']}, ensure_ascii=False))
        if not bundle['items']:
            raise SystemExit('没有取得时间窗口内的有效材料，已保存诊断；未生成或归档日报。')
        return
    issue = read_issue(args.issue)
    if args.command == 'preview':
        print(markdown(issue))
    elif args.command == 'approve':
        if args.scheduled:
            config = json.loads((ROOT / 'config/workflow.json').read_text())
            if config.get('automaticPublicationAuthorized') is not True:
                raise ValueError('尚未授权每日自动出版')
        issue['status'] = 'approved'
        issue['approval'] = {'contentHash': issue_hash(issue), 'approvedAt': timestamp(),
                             'approvedBy': 'user_schedule_authorization' if args.scheduled else 'user',
                             'method': 'scheduled_policy' if args.scheduled else 'explicit_review'}
        write_json(args.issue, issue)
        print('已记录此版本确认；未发送消息。')
    elif args.command == 'export':
        config = json.loads((ROOT / 'config/workflow.json').read_text())
        vault = (ROOT / config['obsidianDirectory']).resolve() if args.obsidian else None
        export_issue(issue, args.output, vault)
        print('日报已导出' + ('并归档到 Obsidian' if vault else '') + '；未发送飞书，未更新网页。')
    elif args.command == 'build-web':
        build_web(issue, args.output)
        print('已生成单独的静态预览文件；未部署。')
    else:
        print('通过：' + issue['id'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit('未完成：' + str(error))
