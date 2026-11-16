"""Attach real source-page images to a draft before approval; never fabricate art."""
import argparse
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import json
from pathlib import Path
import socket
from urllib.parse import urljoin, urlsplit, urlencode
from urllib.request import Request, build_opener, urlopen
from PIL import Image, ImageOps

from feishu import NoRedirect
from newsletter import ROOT, read_issue, write_json
from urllib.error import HTTPError


def safe_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('invalid_public_image_url')
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(a[4][0]) for a in addresses]
    if not ips:
        raise ValueError('non_public_image_host')
    if any(not address.is_global for address in ips):
        # Some local VPNs resolve public sites to RFC 2544 benchmark addresses.
        # Confirm their actual DNS records over authenticated HTTPS; never exempt
        # RFC 1918, loopback, link-local, or other private destinations.
        proxy_range = ipaddress.ip_network('198.18.0.0/15')
        if not all(address in proxy_range for address in ips):
            raise ValueError('non_public_image_host')
        query = urlencode({'name': parts.hostname, 'type': 'A'})
        req = Request('https://cloudflare-dns.com/dns-query?' + query,
                      headers={'Accept': 'application/dns-json'})
        with urlopen(req, timeout=8) as response:
            records = json.loads(response.read(100000))
        actual = [ipaddress.ip_address(a['data']) for a in records.get('Answer', []) if a.get('type') in (1, 28)]
        if records.get('Status') != 0 or not actual or any(not address.is_global for address in actual):
            raise ValueError('non_public_image_host')
    return url


def fetch(url, limit):
    for _ in range(4):
        safe_url(url)
        try:
            with build_opener(NoRedirect).open(Request(url, headers={'User-Agent': 'JORAN-newsletter/1.0'}), timeout=12) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit: raise ValueError('image_response_too_large')
                return raw, url, response.headers.get('Content-Type', '')
        except HTTPError as error:
            if error.code in (301, 302, 303, 307, 308) and error.headers.get('Location'):
                url = urljoin(url, error.headers['Location'])
            else: raise
    raise ValueError('image_redirect_limit')


class SocialImage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('property', attrs.get('name', '')).lower() in ('og:image', 'og:image:url', 'twitter:image', 'twitter:image:src'):
            if attrs.get('content'): self.images.append(attrs['content'])


def normalize_image(raw):
    Image.MAX_IMAGE_PIXELS = 25_000_000
    with Image.open(BytesIO(raw)) as original:
        if original.format not in ('PNG', 'JPEG', 'WEBP'): raise ValueError('unsupported_image_type')
        if original.width < 400 or original.height < 200: raise ValueError('image_too_small')
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1600, 1200))
        out = BytesIO()
        image.save(out, format='JPEG', quality=86, optimize=True)
        return out.getvalue(), image.size


def prepare(path, fetcher=fetch):
    issue = read_issue(path)
    if issue['status'] == 'approved':
        return {'status': 'kept_approved', 'images': sum(bool(a.get('image')) for a in issue['articles'])}
    if issue['status'] != 'draft': raise ValueError('only_draft_images_can_be_prepared')
    results = []
    for article in issue['articles']:
        if article.get('image'):
            image = article['image']
            raw = (ROOT / image['src']).read_bytes()
            actual = hashlib.sha256(raw).hexdigest()
            if image.get('sha256') and image['sha256'] != actual: raise ValueError('image_fingerprint_mismatch')
            image['sha256'] = actual
            results.append({'id': article['id'], 'status': 'kept'})
            continue
        attached = False
        for source in article['sources']:
            try:
                page, page_url, mime = fetcher(source['url'], 2_000_000)
                if 'html' not in mime: continue
                parser = SocialImage()
                parser.feed(page.decode('utf-8', errors='replace'))
                for candidate in parser.images[:3]:
                    try:
                        raw, image_url, _ = fetcher(urljoin(page_url, candidate), 8_000_000)
                        raw, size = normalize_image(raw)
                        digest = hashlib.sha256(raw).hexdigest()
                        name = 'news-' + digest[:24] + '.jpg'
                        (ROOT / 'assets').mkdir(exist_ok=True)
                        (ROOT / 'assets' / name).write_bytes(raw)
                        article['image'] = {'src': 'assets/' + name, 'width': size[0], 'height': size[1],
                            'sha256': digest, 'alt': article['title'], 'sourceUrl': source['url'],
                            'originalUrl': image_url}
                        results.append({'id': article['id'], 'status': 'attached'})
                        attached = True
                        break
                    except Exception:
                        continue
                if attached: break
            except Exception:
                continue
        if not attached: results.append({'id': article['id'], 'status': 'no_usable_source_image'})
    write_json(path, issue)
    return {'status': 'prepared', 'images': sum(bool(a.get('image')) for a in issue['articles']), 'articles': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('issue', type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.issue)
    except Exception:
        result = {'status': 'blocked', 'errorCategory': 'image_preparation_failed'}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result['status'] == 'blocked' else 0)
