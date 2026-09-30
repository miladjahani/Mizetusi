"""A tiny block-style YAML writer — just enough for a Clash profile.

The panel has no YAML dependency and does not want one: the only document it
emits is the Clash/Mihomo profile, whose shape is fixed (mappings, sequences,
strings, numbers, booleans) and whose values are generated here rather than
parsed from anywhere. A full YAML library would add a supply-chain surface and a
parser nothing calls.

What matters instead is that the output is *valid* and *readable*, i.e. it looks
like what every other provider hands out:

* block style only, two-space indentation, with sequence entries at their key's
  own indentation (``proxies:`` then ``- name: …``) — the shape Clash's own docs
  and every working subscription use;
* a scalar is quoted only when it has to be (an indicator character at the start,
  a colon or hash that would change the meaning, non-ASCII text such as the
  Persian country names and the flag emoji), and quoting goes through
  :func:`json.dumps`, whose escapes are valid inside a YAML double-quoted scalar;
* an empty mapping or sequence is written ``{}`` / ``[]`` rather than as a bare
  key, so it never silently becomes ``null``.
"""
import json

# Words YAML would read as something other than the string itself.
_RESERVED = {'true', 'false', 'yes', 'no', 'on', 'off', 'null', 'none', '~'}


def _numeric(text):
    """Whether a plain scalar would be read back as a number rather than a string."""
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _plain(text):
    """Whether ``text`` can be written without quotes."""
    if not text or text != text.strip():
        return False
    if text.lower() in _RESERVED or _numeric(text):
        return False
    if text[0] in '-?:,[]{}#&*!|>\'"%@`':
        return False
    for index, char in enumerate(text):
        code = ord(char)
        if code < 32 or code == 127:
            return False
        # A flag emoji or a Persian country name is not a plain scalar: quoting
        # keeps it out of a parser's way and matches every published profile.
        if code > 126:
            return False
        if char in ':#':
            following = text[index + 1] if index + 1 < len(text) else ''
            if following in ('', ' '):
                return False
    return True


def _scalar(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return value if _plain(value) else json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def _key(value):
    text = str(value)
    return text if _plain(text) else json.dumps(text, ensure_ascii=False)


def _mapping(obj, indent, lines):
    for key, value in obj.items():
        head = ' ' * indent + _key(key) + ':'
        if isinstance(value, dict):
            if value:
                lines.append(head)
                _mapping(value, indent + 2, lines)
            else:
                lines.append(head + ' {}')
        elif isinstance(value, (list, tuple)):
            if value:
                lines.append(head)
                # Indentless: the dashes line up with the key they belong to,
                # which is how Clash's own documentation writes every list.
                _sequence(value, indent, lines)
            else:
                lines.append(head + ' []')
        else:
            lines.append(head + ' ' + _scalar(value))


def _sequence(items, indent, lines):
    for item in items:
        if isinstance(item, dict) and item:
            for index, (key, value) in enumerate(item.items()):
                # The first key sits on the dash line itself; the rest align
                # under it, so every key of one entry is at the same column.
                lead = ' ' * indent + '- ' if index == 0 else ' ' * (indent + 2)
                head = lead + _key(key) + ':'
                if isinstance(value, dict):
                    if value:
                        lines.append(head)
                        _mapping(value, indent + 4, lines)
                    else:
                        lines.append(head + ' {}')
                elif isinstance(value, (list, tuple)):
                    if value:
                        lines.append(head)
                        _sequence(value, indent + 2, lines)
                    else:
                        lines.append(head + ' []')
                else:
                    lines.append(head + ' ' + _scalar(value))
        else:
            lines.append(' ' * indent + '- ' + _scalar(item))


def dump(document):
    """Serialise a document (dict/list/scalar tree) as block-style YAML text."""
    lines = []
    if isinstance(document, dict):
        _mapping(document, 0, lines)
    elif isinstance(document, (list, tuple)):
        _sequence(document, 0, lines)
    else:
        lines.append(_scalar(document))
    return '\n'.join(lines) + '\n'
