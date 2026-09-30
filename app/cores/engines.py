"""Each engine's own server config, and how to validate or run it.

The two engines agree on almost nothing, so each gets its own renderer:

* **sing-box** takes JSON, an ``inbounds`` list, and a ``users`` **array** of
  ``{name, password}`` objects (TUIC adds ``uuid`` plus a congestion control).
* **mihomo** takes YAML and a ``listeners`` list where the users are a **map** —
  AnyTLS: ``username: password``, TUIC: ``uuid: password``. mihomo really does put
  the UUID *in the key* for TUIC (checked against its sources: ``Users
  map[string]string`` is read as ``uuid -> password``), which is exactly the kind
  of detail that makes a hand-written config silently refuse every client.

What they share is the result: one listener per enabled protocol carrying one
credential per active user, so a hosted protocol is a real per-user node rather
than one shared secret. Both accept the certificate **inline**, so nothing but
the engine's own config file has to be written.

The small YAML writer lives here instead of a PyYAML dependency: the project's
requirements file is the deployment contract, and the shape below needs nothing
more than nested maps, lists and block scalars.
"""
import datetime
import json
import os

from app.config import settings
from app.cores import profiles

SINGBOX = 'singbox'
MIHOMO = 'mihomo'

LABELS = {'singbox': 'sing-box', 'mihomo': 'mihomo'}


def label(engine):
    return LABELS.get(str(engine or '').strip().lower(), str(engine or ''))


def binary(engine):
    return settings.singbox_binary if engine == SINGBOX else settings.mihomo_binary


def config_path(engine):
    return settings.singbox_config if engine == SINGBOX else settings.mihomo_config


def home(engine):
    """The working directory an engine keeps its cache in (mihomo needs one)."""
    return settings.mihomo_home if engine == MIHOMO else os.path.dirname(settings.singbox_config)


def available(engine):
    """Whether this engine's binary is actually installed."""
    if engine not in (SINGBOX, MIHOMO):
        return False
    return os.path.exists(binary(engine))


def validate_argv(engine, path):
    """How to ask the engine itself whether a config is usable.

    Both engines can validate without binding a port, which is what keeps one bad
    config from ever reaching a running process.
    """
    if engine == SINGBOX:
        return [binary(engine), 'check', '-c', path]
    return [binary(engine), '-d', home(engine), '-f', path, '-t']


def run_argv(engine, path):
    if engine == SINGBOX:
        return [binary(engine), 'run', '-c', path]
    return [binary(engine), '-d', home(engine), '-f', path]


# ------------------------------------------------------------------ certificate
def certificate(sni, months=120):
    """A self-signed TLS pair for the hosted protocols.

    AnyTLS and TUIC terminate TLS themselves, so unlike every WebSocket transport
    (where the platform terminates it) these need a real key pair. It is issued
    for the disguise name the links already carry as ``sni`` and the links are
    published with ``insecure``, exactly like a hysteria2 server does — there is
    no way to get a public certificate for an address the admin has not
    pointed a domain at.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    name = str(sni or 'www.cloudflare.com')
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30 * int(months or 120)))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    cert = builder.sign(key, hashes.SHA256())
    return {
        'cert': cert.public_bytes(serialization.Encoding.PEM).decode(),
        'key': key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode(),
        'sni': name,
    }


# ---------------------------------------------------------------------- configs
def _singbox_inbound(item):
    """One hosted protocol as a sing-box inbound."""
    profile = item['profile']
    protocol = profile['protocol']
    users = []
    for name, uuid in item['users']:
        if protocol == profiles.TUIC:
            # A TUIC user is a UUID *and* a password; the panel gives both the
            # user's one credential so a client link needs no second secret.
            users.append({'name': name, 'uuid': uuid, 'password': uuid})
        else:
            users.append({'name': name, 'password': uuid})
    inbound = {
        'type': protocol, 'tag': 'core-' + profile['id'], 'listen': '0.0.0.0',
        'listen_port': int(item['port']), 'users': users,
        'tls': {'enabled': True, 'server_name': item['sni'],
                'certificate': [item['cert']], 'key': [item['key']]},
    }
    if protocol == profiles.TUIC:
        # Matches the ``congestion_control`` the published links ask for, so the
        # client and the server agree instead of falling back to a default.
        inbound['congestion_control'] = 'bbr'
    return inbound


def _mihomo_listener(item):
    """One hosted protocol as a mihomo listener (users are a map here)."""
    profile = item['profile']
    protocol = profile['protocol']
    users = {}
    for name, uuid in item['users']:
        users[uuid if protocol == profiles.TUIC else name] = uuid
    return {
        'name': 'core-' + profile['id'], 'type': protocol, 'listen': '0.0.0.0',
        'port': int(item['port']), 'users': users,
        'certificate': item['cert'], 'private-key': item['key'],
    }


def singbox_config(listeners):
    return {
        'log': {'level': 'warn'},
        'inbounds': [_singbox_inbound(item) for item in listeners],
        'outbounds': [{'type': 'direct', 'tag': 'direct'}],
    }


def mihomo_config(listeners, api=''):
    config = {
        'log-level': 'warning', 'mode': 'rule', 'mixed-port': 0,
        'listeners': [_mihomo_listener(item) for item in listeners],
        # A single MATCH rule: no GEOIP/geosite rule means mihomo never has to
        # fetch a geodata file before it can serve.
        'rules': ['MATCH,DIRECT'],
    }
    if api:
        config['external-controller'] = api
    return config


def config_text(engine, listeners, api=''):
    """The config as the engine wants it: JSON for sing-box, YAML for mihomo."""
    if engine == SINGBOX:
        return json.dumps(singbox_config(listeners), ensure_ascii=False, indent=2) + '\n'
    return _yaml(mihomo_config(listeners, api)) + '\n'


# ------------------------------------------------------------------ yaml writer
def _scalar(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if (not text or text != text.strip() or text.lower() in ('true', 'false', 'null', 'yes', 'no', 'on', 'off', '~')
            or any(char in text for char in ':#{}[]&*!|>\'"%@`,')):
        return "'" + text.replace("'", "''") + "'"
    return text


def _value(value, indent):
    if isinstance(value, str) and '\n' in value.strip():
        # A PEM block becomes a literal block scalar; ``|`` clips to one trailing
        # newline, so the value is emitted without its own.
        body = '\n'.join('  ' * (indent + 1) + line for line in value.strip('\n').split('\n'))
        return '|\n' + body
    return _scalar(value)


def _yaml(value, indent=0):
    pad = '  ' * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f'{pad}{key}:')
                lines.append(_yaml(item, indent + 1))
            elif isinstance(item, (dict, list)):
                lines.append(f'{pad}{key}: ' + ('{}' if isinstance(item, dict) else '[]'))
            else:
                lines.append(f'{pad}{key}: {_value(item, indent)}')
        return '\n'.join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict) and item:
                block = _yaml(item, indent + 1).split('\n')
                lines.append(f'{pad}- {block[0].lstrip()}')
                lines.extend(block[1:])
            else:
                lines.append(f'{pad}- {_value(item, indent)}')
        return '\n'.join(lines)
    return f'{pad}{_value(value, indent)}'
