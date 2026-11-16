"""Render the canonical issue as image/text cards; no uploads or sending here."""
import json
import re

from newsletter import markdown


def image_card_payloads(issue, keyword, image_keys):
    # Reuse exactly the same body and sources as all other outputs.
    sections = markdown(issue).split('\n---\n')
    groups = [[{'tag': 'markdown', 'content': sections[0].strip()}]]
    for article, section in zip(issue['articles'], sections[1:-1]):
        heading, _, body = section.strip().partition('\n\n')
        elements = [{'tag': 'markdown', 'content': heading}]
        image = article.get('image')
        if image:
            key = image_keys.get(article['id'])
            if not isinstance(key, str) or not re.fullmatch(r'img_[A-Za-z0-9_-]+', key):
                raise ValueError('配图尚未上传或缺少有效图片 key，未发送图文日报')
            elements.append({'tag': 'img', 'img_key': key,
                             'alt': {'tag': 'plain_text', 'content': image['alt']},
                             'scale_type': 'fit_horizontal', 'preview': True})
        elements.append({'tag': 'markdown', 'content': body})
        groups.append(elements)
    groups.append([{'tag': 'markdown', 'content': sections[-1].strip()}])

    def payload(elements, suffix=''):
        title = f"每日 AI · {issue['date']}" + suffix
        if keyword and keyword not in title:
            title = keyword + ' · ' + title
        return {'msg_type': 'interactive', 'card': {'schema': '2.0',
                'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': 'blue'},
                'body': {'elements': elements}}}

    def fits(elements):
        # Reserve room for the part label, keyword, and future signing fields.
        return (len(elements) <= 190 and
                len(json.dumps(payload(elements), ensure_ascii=False).encode()) <= 18000)

    chunks, current = [], []
    for group in groups:
        candidate = current + ([{'tag': 'hr'}] if current else []) + group
        if current and not fits(candidate):
            chunks.append(current)
            current = list(group)
        else:
            current = candidate
        if not fits(current):
            raise ValueError('单则图文新闻超过卡片限制，尚未发送')
    if current:
        chunks.append(current)
    result = [payload(chunk, f'（{i}/{len(chunks)}）' if len(chunks) > 1 else '')
              for i, chunk in enumerate(chunks, 1)]
    if any(len(json.dumps(p, ensure_ascii=False).encode()) > 19000 for p in result):
        raise ValueError('图文卡片超过本地限制，尚未发送')
    return result
