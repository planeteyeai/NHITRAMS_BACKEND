"""Accept-Encoding negotiation helpers for inventory_filter direct-byte responses.

Django's GZipMiddleware uses a simple ``\\bgzip\\b`` regex and does **not** honor
``gzip;q=0``. Direct-byte serving must correctly parse quality values so we never
send pre-compressed bodies when the client forbids gzip.
"""

from __future__ import annotations


def accepts_gzip_encoding(accept_encoding: str | None) -> bool:
    """Return True if Accept-Encoding allows gzip with q > 0.

    Handles missing/empty headers, ``gzip``, ``gzip;q=…``, ``*``, and lists such as
    ``gzip, deflate, br``. ``gzip;q=0`` / ``*;q=0`` return False.
    """
    if not accept_encoding:
        return False
    header = accept_encoding.strip()
    if not header:
        return False

    for part in header.split(','):
        token = part.strip()
        if not token:
            continue
        name, sep, param_str = token.partition(';')
        coding = name.strip().lower()
        q = 1.0
        if sep:
            for param in param_str.split(';'):
                param = param.strip()
                if not param:
                    continue
                key, eq, value = param.partition('=')
                if eq and key.strip().lower() == 'q':
                    try:
                        q = float(value.strip())
                    except ValueError:
                        q = 0.0
                    break
        if coding in ('gzip', '*') and q > 0.0:
            return True
    return False
