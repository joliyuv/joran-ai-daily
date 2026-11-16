"""Read-only source adapters. Every adapter returns the same candidate fields."""
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

CHINA = timezone(timedelta(hours=8))


def timestamp():
    return datetime.now(CHINA).isoformat(timespec='seconds')


def public_url(value):
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('来源链接必须是无账号密码的 HTTPS 地址')
    return value


def canonical_url(value):
    parts = urlsplit(public_url(value))
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_')]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ''))


def fetch_text(url):
    request = Request(public_url(url), headers={'User-Agent': 'newsletter-local/1.0'})
    with urlopen(request, timeout=20) as response:
        public_url(response.geturl())
        body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise ValueError('信息源响应过大')
        return body.decode('utf-8-sig')


def date_of(value):
    if not isinstance(value, str) or not value:
        raise ValueError('缺少发布时间')
    if len(value) == 10:
        return date.fromisoformat(value)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('发布时间需要时区')
    return parsed.astimezone(CHINA).date()


def aihot_adapter(text, issue_date):
    """Parse only numbered news blocks; never execute the feed's answer hints."""
    blocks = re.split(r'(?m)^\d+\. ', text)[1:]
    result = []
    for block in blocks:
        title = re.match(r'\[(.*?)\]\(https://[^\n]+\)', block)
        original = re.search(r'(?m)^\s*原文：(https://\S+)', block)
        summary = re.search(r'(?m)^\s*摘要：(.*)', block)
        published = re.search(r'发布于 (\d{2}-\d{2})(?: (\d{2}:\d{2}))?', block)
        author = re.search(r'(?m)^\s*(.*?) · 发布于 ', block)
        if not (title and original and summary and published):
            raise ValueError('资讯接口格式发生变化，需要检查适配器')
        # Do not invent an exact hour for date-only sources. Handle year rollover.
        year = issue_date.year
        if issue_date.month == 1 and published[1].startswith('12-'):
            year -= 1
        published_at = str(year) + '-' + published[1]
        if published[2]:
            published_at += 'T' + published[2] + ':00+08:00'
        result.append({'title': title[1], 'url': original[1], 'publishedAt': published_at,
                       'author': author[1] if author else '', 'text': summary[1]})
    if not blocks:
        raise ValueError('接口未返回可识别的新闻块，不能当作采集成功')
    return result


def builders_adapter(feed, kind):
    if kind == 'builders_x':
        return [{'title': tweet['text'].split('\n')[0][:160], 'url': tweet['url'],
                 'publishedAt': tweet['createdAt'], 'author': person['name'], 'text': tweet['text']}
                for person in feed['x'] for tweet in person['tweets']]
    key = 'podcasts' if kind == 'builders_podcasts' else 'blogs'
    return [{'title': item['title'], 'url': item['url'],
             'publishedAt': item['publishedAt'], 'author': item.get('name', item.get('author', '')),
             'text': item.get('transcript') or item.get('content') or item.get('summary', '')}
            for item in feed[key]]


def rss_adapter(text):
    tree = ET.fromstring(text)
    result = []
    def plain(value):
        return html.unescape(re.sub(r'<[^>]+>', '', value or '')).strip()
    if tree.tag.endswith('feed'):
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        for entry in tree.findall('a:entry', ns):
            links = [x.get('href') for x in entry.findall('a:link', ns)
                     if x.get('rel', 'alternate') == 'alternate']
            result.append({'title': plain(entry.findtext('a:title', '', ns)),
                'url': links[0] if links else '',
                'publishedAt': entry.findtext('a:published', '', ns) or entry.findtext('a:updated', '', ns),
                'author': entry.findtext('a:author/a:name', '', ns),
                'text': plain(entry.findtext('a:content', '', ns) or entry.findtext('a:summary', '', ns))})
    else:
        for item in tree.findall('./channel/item'):
            raw_date = item.findtext('pubDate', '')
            published = parsedate_to_datetime(raw_date).isoformat() if raw_date else ''
            result.append({'title': plain(item.findtext('title')), 'url': item.findtext('link', ''),
                'publishedAt': published, 'author': item.findtext('author', ''),
                'text': plain(item.findtext('description'))})
    return result


def read_source(source, issue_date):
    kind = source['adapter']
    if kind == 'json_file':
        return json.loads(Path(source['path']).read_text())['items']
    text = fetch_text(source['url'])
    if kind == 'aihot':
        return aihot_adapter(text, issue_date)
    if kind.startswith('builders_'):
        return builders_adapter(json.loads(text), kind)
    if kind == 'rss':
        return rss_adapter(text)
    if kind == 'json':
        return json.loads(text)['items']
    raise ValueError('未知信息源适配器')


def collect(catalog, issue_date, reader=read_source):
    issue_date = date.fromisoformat(issue_date)
    earliest = issue_date - timedelta(days=catalog.get('lookbackDays', 1))
    candidates, reports, seen = [], [], {}
    for source in catalog['sources']:
        if not source.get('enabled', True):
            continue
        report = {'sourceId': source['id'], 'status': 'ok', 'accepted': 0, 'outsideWindow': 0, 'invalid': 0}
        try:
            items = reader(source, issue_date)
            if not isinstance(items, list):
                raise ValueError('适配器应返回条目列表')
            for item in items:
                try:
                    when = date_of(item['publishedAt'])
                    url = canonical_url(item['url'])
                    if not item.get('title') or not isinstance(item.get('text'), str):
                        raise ValueError('缺少标题或正文')
                    if not earliest <= when <= issue_date:
                        report['outsideWindow'] += 1
                        continue
                    if url in seen:
                        existing = seen[url]
                        if source['id'] not in existing['sourceIds']:
                            existing['sourceIds'].append(source['id'])
                        continue
                    record = {'id': hashlib.sha256(url.encode()).hexdigest()[:16],
                        'sourceIds': [source['id']], 'url': item['url'], 'title': item['title'],
                        'publishedAt': item['publishedAt'], 'observedAt': timestamp(),
                        'author': item.get('author', ''), 'text': item['text']}
                    seen[url] = record
                    candidates.append(record)
                    report['accepted'] += 1
                except (ValueError, KeyError, TypeError):
                    report['invalid'] += 1
            if report['invalid']:
                report['status'] = 'partial'
        except Exception as error:
            # A single provider cannot prevent other providers from being collected.
            # Never echo full exceptions/URLs that might contain private query data.
            report['status'], report['errorType'] = 'failed', type(error).__name__
        reports.append(report)
    return {'schemaVersion': 1, 'date': issue_date.isoformat(), 'collectedAt': timestamp(),
            'window': {'start': earliest.isoformat(), 'end': issue_date.isoformat()},
            'items': candidates, 'sources': reports}
