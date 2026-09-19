"""Ready-made node examples for the Nodes section.

A node in NEXUS is generic — ``server``/``port``/``tls``/``sni``/``host`` — so the
same origin can be published through a clean IP on an alternative port, through a
clean domain, through a different CDN, or without TLS at all. Assembling those by
hand means knowing the addresses and the ports off the top of one's head, so this
module ships the combinations that are actually worth having:

* **clean IPs** — Cloudflare, Fastly, Gcore and آروان‌کلود anycast addresses, each
  dialled by IP with the deployment's own hostname as Host/SNI;
* **alternative ports** — Cloudflare answers HTTPS on 2053/2087/2096/8443, which is
  what unblocks a network that drops 443;
* **clean domains** — the deployment's own hostname published as a node, which is
  exactly what a custom domain behind a CDN needs;
* **plain WebSocket** — the same host over port 80 with TLS off, as a last resort;
* **the origin** — the deployment's own node, re-creatable if it was deleted.

They are *examples*, not magic: adding one never overwrites an existing node with
the same name, ``Host``/``SNI`` default to the address the CDN really serves this
deployment on (the Worker URL when one is configured, else the public host), and
each entry carries a note telling the admin what to change for their own setup.
"""

# Fixed anycast addresses taken from each provider's published ranges
# (https://www.cloudflare.com/ips-v4, https://api.fastly.com/public-ip-list,
#  https://api.gcore.com/cdn/public-ip-list, https://www.arvancloud.ir/fa/ips.txt).
CLOUDFLARE_IP = '104.16.1.1'
CLOUDFLARE_IP_ALT = '172.64.155.209'
FASTLY_IP = '151.101.1.69'
GCORE_IP = '92.223.74.21'
ARVAN_IP = '185.143.232.20'

# Cloudflare terminates TLS on these as well as 443 — the point of the "alt port"
# examples: a network that drops 443 still lets the same edge through.
CLOUDFLARE_ALT_PORTS = (2053, 2087, 2096, 8443)

# Field templates: ``{host}`` is the domain the CDN serves this deployment on.
SAMPLES = (
    {
        'id': 'clean-domain', 'label': 'دامنهٔ تمیز (دامنهٔ خود پنل)',
        'note': 'دامنهٔ خود این سرویس را به‌عنوان یک نود منتشر می‌کند — همان چیزی که برای '
                'دامنهٔ اختصاصی پشت CDN لازم است. اگر دامنهٔ تمیز دیگری دارید، server وHost آن را عوض کنید.',
        'tags': ['دامنه', 'host=SNI'],
        'host_mode': 'worker',
        'node': {'name': 'sample-clean-domain', 'kind': 'edge', 'server': '{host}', 'port': 443,
                 'tls': 1, 'sni': '{host}', 'host': '{host}', 'location': 'domain', 'provider': 'domain'},
    },
    {
        'id': 'cf-worker', 'label': 'Cloudflare · آی‌پی تمیز + Worker',
        'note': 'کلاسیک‌ترین حالت: آی‌پی تمیز کلودفلر با Worker به‌عنوان Host/SNI. پرسرعت‌ترین '
                'گزینه روی شبکه‌های ایرانی.',
        'tags': ['Cloudflare', 'پورت ۴۴۳'],
        'host_mode': 'worker',
        'node': {'name': 'sample-cf-worker', 'kind': 'cloudflare', 'server': CLOUDFLARE_IP, 'port': 443,
                 'tls': 1, 'sni': '{host}', 'host': '{host}', 'location': 'cf', 'provider': 'cloudflare'},
    },
    {
        'id': 'cf-alt-ports', 'label': 'Cloudflare · پورت‌های جایگزین ۲۰۵۳/۲۰۸۷/۲۰۹۶/۸۴۴۳',
        'note': 'کلودفلر روی این پورت‌ها هم TLS می‌دهد؛ اگر شبکه‌ای پورت ۴۴۳ را بسته باشد، این نودها '
                'دقیقاً همان لبه را از راه دیگری می‌گیرند.',
        'tags': ['Cloudflare', 'fallback 443'],
        'host_mode': 'worker',
        'expand': [{'name': f'sample-cf-{port}', 'kind': 'cloudflare', 'server': CLOUDFLARE_IP_ALT, 'port': port,
                    'tls': 1, 'sni': '{host}', 'host': '{host}', 'location': 'cf', 'provider': 'cloudflare'}
                   for port in CLOUDFLARE_ALT_PORTS],
    },
    {
        'id': 'cf-sni-mismatch', 'label': 'Cloudflare · آی‌پی و SNI متفاوت (ضد بلاک)',
        'note': 'آی‌پی از یک رنج و SNI از رنج دیگر؛ وقتی یک رنج خاص در شبکه‌ای سوخته باشد مفید است. '
                'SNI را به دامنهٔ تمیز خودتان تغییر دهید.',
        'tags': ['Cloudflare', 'آی‌پی/SNI متفاوت'],
        'host_mode': 'worker',
        'node': {'name': 'sample-cf-sni', 'kind': 'cloudflare', 'server': '188.114.96.1', 'port': 443,
                 'tls': 1, 'sni': '{host}', 'host': '{host}', 'location': 'cf', 'provider': 'cloudflare'},
    },
    {
        'id': 'fastly', 'label': 'Fastly · آی‌پی تمیز',
        'note': 'شبکهٔ anycast Fastly؛ در بسیاری از شبکه‌های ایران پینگ پایین‌تری از کلودفلر دارد. '
                'دامنه را باید پشت Fastly داشته باشید.',
        'tags': ['Fastly', 'لوکیشن جدا'],
        'node': {'name': 'sample-fastly', 'kind': 'cloudflare', 'server': FASTLY_IP, 'port': 443,
                 'tls': 1, 'sni': '{host}', 'host': '{host}', 'location': 'fastly', 'provider': 'fastly'},
    },
    {
        'id': 'gcore', 'label': 'Gcore · آی‌پی تمیز',
        'note': 'لبهٔ Gcore؛ تنوع جغرافیایی بیشتر برای کاربرانی که کلودفلر برایشان اشباع است.',
        'tags': ['Gcore', 'لوکیشن جدا'],
        'node': {'name': 'sample-gcore', 'kind': 'cloudflare', 'server': GCORE_IP, 'port': 443,
                 'tls': 1, 'sni': '{host}', 'host': '{host}', 'location': 'gcore', 'provider': 'gcore'},
    },
    {
        'id': 'arvan', 'label': 'آروان‌کلود · آی‌پی ایران',
        'note': 'لبهٔ آروان‌کلود داخل ایران؛ برای کاربرانی که آی‌پی‌های خارجی برایشان کند یا بسته است.',
        'tags': ['آروان', 'لوکیشن ایران'],
        'node': {'name': 'sample-arvan', 'kind': 'cloudflare', 'server': ARVAN_IP, 'port': 443,
                 'tls': 1, 'sni': '{host}', 'host': '{host}', 'location': 'ir', 'provider': 'arvancloud'},
    },
    {
        'id': 'plain-ws', 'label': 'بدون TLS · پورت ۸۰ (فالبک نهایی)',
        'note': 'همین دامنه روی پورت ۸۰ و بدون TLS. فقط اگر میزبانی که استفاده می‌کنید WebSocket خام را '
                'روی ۸۰ سرو می‌کند (روی بیشتر PaaSها به HTTPS ریدایرکت می‌شود) — وگرنه این نود را خاموش کنید.',
        'tags': ['بدون TLS', 'پورت ۸۰'],
        'host_mode': 'worker',
        'node': {'name': 'sample-plain-ws', 'kind': 'edge', 'server': '{host}', 'port': 80,
                 'tls': 0, 'sni': '', 'host': '{host}', 'location': '', 'provider': 'domain'},
    },
    {
        'id': 'origin', 'label': 'نود مستقیم همین سرویس (Origin)',
        'note': 'بهترین مسیرِ همیشه‌موجود: دامنهٔ خود سرویس با TLS کامل. اگر نود Origin را حذف کرده‌اید، '
                'از اینجا برمی‌گردد (و Sync هم خودش می‌سازد).',
        'tags': ['Origin', 'همیشه فعال'],
        'host_mode': 'public',
        'node': {'name': 'sample-origin', 'kind': 'railway', 'server': '{host}', 'port': 443,
                 'tls': 1, 'sni': '{host}', 'host': '{host}', 'location': '', 'provider': 'origin'},
    },
)

SAMPLE_META = {'role': 'sample'}


def _resolve(node, host):
    """Substitute the ``{host}`` placeholder in one node definition."""
    out = {}
    for key, value in node.items():
        out[key] = value.replace('{host}', host or '') if isinstance(value, str) else value
    return out


def catalog(public_host='', worker_url=''):
    """The samples, with Host/SNI resolved for this deployment.

    ``host_mode`` decides what becomes Host/SNI, because the honest default
    differs per sample:

    * ``worker`` (Cloudflare samples, the clean domain, the plain-WS fallback) —
      the Worker hostname when one is configured, since that is the domain the
      CDN really serves this origin on;
    * ``public`` (the origin node, and the other CDNs) — the deployment's own
      hostname first: Fastly or آروان will not serve a ``*.workers.dev`` SNI, so
      pointing at it would be a link that cannot validate.
    """
    from app.edge.sources import urllib_url_host
    worker_host = urllib_url_host(worker_url) or ''
    public = (public_host or '').strip()
    items = []
    for spec in SAMPLES:
        host = worker_host or public
        if spec.get('host_mode') != 'worker' and public:
            host = public if public else worker_host
        nodes = spec.get('expand') or [spec['node']]
        resolved = [_resolve(node, host) for node in nodes]
        items.append({
            'id': spec['id'],
            'label': spec['label'],
            'note': spec['note'],
            'tags': list(spec.get('tags') or []),
            'host': host,
            'node': resolved[0],
            'nodes': resolved,
            'names': [node['name'] for node in resolved],
        })
    return items


def find(sample_id):
    wanted = str(sample_id or '').strip().lower()
    for spec in SAMPLES:
        if spec['id'] == wanted:
            return spec
    return None


def nodes_for(sample_id, public_host='', worker_url=''):
    """The node definitions one sample creates (``None`` for an unknown id)."""
    wanted = str(sample_id or '').strip().lower()
    for item in catalog(public_host, worker_url):
        if item['id'] == wanted:
            return item['nodes']
    return None


def is_sample(node):
    """Whether a node row was created from a sample (metadata is JSON or repr)."""
    from app.edge.sources import parse_metadata
    meta = parse_metadata((node or {}).get('metadata'))
    return str(meta.get('role') or '') == 'sample'
