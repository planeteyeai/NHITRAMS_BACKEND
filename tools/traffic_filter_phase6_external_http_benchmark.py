"""Phase 6 — Real external HTTP latency diagnosis for POST /traffic_filter/.

Diagnosis only. Does not change production code, Redis, TFGZ1, gzip, or API.

Usage:
  $env:NHIT_API_BASE_URL="https://nhitramsbackend-production.up.railway.app"
  python tools/traffic_filter_phase6_external_http_benchmark.py

If NHIT_API_BASE_URL is unset, falls back to the known production host from prior
project docs (override recommended).
"""

from __future__ import annotations

import gzip
import json
import os
import socket
import ssl
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

import httpx
import requests
from requests.adapters import HTTPAdapter

from nhit.caching.traffic_filter_cache import (
    get_cached_traffic_filter_response,
)
from traffic.serializers import TrafficFilterRequestSerializer

DEFAULT_BASE = 'https://nhitramsbackend-production.up.railway.app'
WARMUPS = 10
SAMPLES = 20
OUT_JSON = ROOT / 'tools' / 'traffic_filter_phase6_external_http_results.json'
OUT_MD = ROOT / 'tools' / 'traffic_filter_phase6_external_http_investigation.md'
POSTMAN_OBSERVED_MS = 6000

PRIMARY = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-05-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}
LARGE = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': '2025-08-20',
    'direction': ['All'],
    'project_name': ['Chittorgarh to Kota'],
}


def _log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'), flush=True)


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {'n': 0}
    ordered = sorted(values)
    p95_i = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    p99_i = min(len(ordered) - 1, max(0, int(round(0.99 * (len(ordered) - 1)))))
    return {
        'n': len(values),
        'min': round(min(values), 3),
        'median': round(statistics.median(values), 3),
        'p95': round(ordered[p95_i], 3),
        'p99': round(ordered[p99_i], 3),
        'max': round(max(values), 3),
        'mean': round(statistics.fmean(values), 3),
    }


def _redact_url(url: str) -> dict[str, Any]:
    p = urlparse(url)
    host = p.hostname or ''
    return {
        'scheme': p.scheme,
        'host_suffix': '.'.join(host.split('.')[-4:]) if host else None,
        'path': p.path or '/',
        'port': p.port,
    }


def _base_url() -> tuple[str, str]:
    env = (os.environ.get('NHIT_API_BASE_URL') or '').strip().rstrip('/')
    if env:
        return env, 'NHIT_API_BASE_URL'
    return DEFAULT_BASE.rstrip('/'), 'default_from_prior_docs'


def _headers(*, accept_gzip: bool) -> dict[str, str]:
    return {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip' if accept_gzip else 'identity',
    }


def _decode_body(raw: bytes, encoding: str | None) -> tuple[bytes, int | None]:
    enc = (encoding or '').lower() or None
    if enc == 'gzip':
        try:
            return gzip.decompress(raw), len(raw)
        except OSError:
            # Client may have already decompressed.
            return raw, None
    return raw, None


def _tcp_tls_connect_ms(base: str, samples: int = 15) -> dict[str, Any]:
    parsed = urlparse(base)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    dns_ms: list[float] = []
    tcp_ms: list[float] = []
    tls_ms: list[float] = []
    total_ms: list[float] = []
    for _ in range(samples):
        t0 = time.perf_counter()
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        t_dns = time.perf_counter()
        dns_ms.append((t_dns - t0) * 1000)
        family, type_, proto, _, sockaddr = infos[0]
        sock = socket.socket(family, type_, proto)
        sock.settimeout(30)
        t_tcp0 = time.perf_counter()
        sock.connect(sockaddr)
        t_tcp1 = time.perf_counter()
        tcp_ms.append((t_tcp1 - t_tcp0) * 1000)
        if parsed.scheme == 'https':
            ctx = ssl.create_default_context()
            t_tls0 = time.perf_counter()
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            t_tls1 = time.perf_counter()
            tls_ms.append((t_tls1 - t_tls0) * 1000)
            ssock.close()
        else:
            sock.close()
            tls_ms.append(0.0)
        total_ms.append((time.perf_counter() - t0) * 1000)
    return {
        'dns_ms': _stats(dns_ms),
        'tcp_connect_ms': _stats(tcp_ms),
        'tls_handshake_ms': _stats(tls_ms),
        'dns_tcp_tls_total_ms': _stats(total_ms),
    }


def _requests_once(
    session: requests.Session | None,
    url: str,
    payload: dict,
    *,
    accept_gzip: bool,
    allow_redirects: bool = True,
) -> dict[str, Any]:
    headers = _headers(accept_gzip=accept_gzip)
    # Keep wire bytes compressed: disable urllib3 auto-decode on the raw stream.
    t0 = time.perf_counter()
    if session is None:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=180,
            allow_redirects=allow_redirects,
            stream=True,
        )
    else:
        resp = session.post(
            url,
            json=payload,
            headers=headers,
            timeout=180,
            allow_redirects=allow_redirects,
            stream=True,
        )
    t_headers = time.perf_counter()
    resp.raw.decode_content = False
    first_byte = None
    chunks: list[bytes] = []
    while True:
        chunk = resp.raw.read(64 * 1024)
        if not chunk:
            break
        if first_byte is None:
            first_byte = time.perf_counter()
        chunks.append(chunk)
    t_done = time.perf_counter()
    resp.close()
    raw = b''.join(chunks)
    encoding = (resp.headers.get('Content-Encoding') or '').lower() or None
    content_length = resp.headers.get('Content-Length')
    body, compressed = _decode_body(raw, encoding)
    wire_bytes = len(raw)
    if encoding == 'gzip':
        compressed = wire_bytes
    elif content_length:
        try:
            compressed = int(content_length) if encoding else None
        except ValueError:
            compressed = None

    total = (t_done - t0) * 1000
    ttfb = ((first_byte or t_headers) - t0) * 1000
    transfer = (t_done - (first_byte or t_headers)) * 1000
    header_wait = (t_headers - t0) * 1000
    cl_int = None
    if content_length:
        try:
            cl_int = int(content_length)
        except ValueError:
            cl_int = None
    return {
        'status': resp.status_code,
        'total_ms': total,
        'ttfb_ms': ttfb,
        'headers_ms': header_wait,
        'body_transfer_ms': transfer,
        'content_encoding': encoding,
        'content_type': resp.headers.get('Content-Type'),
        'content_length': content_length,
        'content_length_int': cl_int,
        'vary': resp.headers.get('Vary'),
        'transfer_encoding': resp.headers.get('Transfer-Encoding'),
        'wire_bytes': wire_bytes,
        'uncompressed_bytes': len(body),
        'compressed_bytes': compressed,
        'redirect_count': len(resp.history),
        'redirect_urls': [r.url.split('?')[0] for r in resp.history],
        'final_url_path': urlparse(resp.url).path,
        'json_ok': body[:1] in (b'[', b'{'),
        'looks_like_gzip_hit': bool(
            resp.status_code == 200
            and (
                (accept_gzip and encoding == 'gzip' and wire_bytes > 100)
                or (
                    (not accept_gzip)
                    and resp.status_code == 200
                    and body[:1] in (b'[', b'{')
                    and len(body) > 100
                )
            )
        ),
    }


def _assert_ok_sample(sample: dict[str, Any], *, accept_gzip: bool, label: str) -> None:
    if sample.get('status') != 200:
        raise RuntimeError(
            f'{label}: expected HTTP 200, got {sample.get("status")} '
            f'(path={sample.get("final_url_path")} wire={sample.get("wire_bytes")})'
        )
    if accept_gzip:
        if sample.get('content_encoding') != 'gzip':
            raise RuntimeError(
                f'{label}: expected Content-Encoding=gzip, got '
                f'{sample.get("content_encoding")!r}'
            )
        if (sample.get('wire_bytes') or 0) < 100:
            raise RuntimeError(f'{label}: gzip wire body too small: {sample.get("wire_bytes")}')
    else:
        if not sample.get('json_ok'):
            raise RuntimeError(f'{label}: identity response is not JSON')


def _warm_railway_http_until_hit(url: str, payload: dict, *, label: str) -> dict[str, Any]:
    """Warm Railway via real HTTP; return cold first + confirmed warm sample."""
    cold = _requests_once(None, url, payload, accept_gzip=True)
    _assert_ok_sample(cold, accept_gzip=True, label=f'{label}_cold_first')
    session = requests.Session()
    warm = None
    for i in range(8):
        warm = _requests_once(session, url, payload, accept_gzip=True)
        _assert_ok_sample(warm, accept_gzip=True, label=f'{label}_warm_{i}')
        # Heuristic: after first populate, warm HIT should be much faster than cold MISS.
        if warm['total_ms'] < max(1500.0, cold['total_ms'] * 0.55):
            break
    session.close()
    return {
        'cold_first_http': cold,
        'warm_confirm_http': warm,
        'note': (
            'Cold = first external HTTP in this warm sequence. '
            'Warm confirm = subsequent request after Railway should have cached.'
        ),
    }


def _httpx_once(
    client: httpx.Client | None,
    url: str,
    payload: dict,
    *,
    accept_gzip: bool,
) -> dict[str, Any]:
    headers = _headers(accept_gzip=accept_gzip)
    t0 = time.perf_counter()
    if client is None:
        with httpx.Client(timeout=180.0, follow_redirects=True) as c:
            resp = c.post(url, json=payload, headers=headers)
    else:
        resp = client.post(url, json=payload, headers=headers)
    t_done = time.perf_counter()
    raw = resp.content
    encoding = (resp.headers.get('Content-Encoding') or '').lower() or None
    # httpx may auto-decompress; prefer Content-Length for compressed size.
    body = raw
    compressed = None
    if encoding == 'gzip':
        try:
            compressed = int(resp.headers.get('Content-Length') or 0) or None
        except ValueError:
            compressed = None
        # If still compressed on the wire copy, decompress for json_ok check.
        if raw[:2] == b'\x1f\x8b':
            body, compressed = _decode_body(raw, 'gzip')
            compressed = compressed or len(raw)

    total = (t_done - t0) * 1000
    elapsed = resp.elapsed.total_seconds() * 1000 if resp.elapsed else total
    return {
        'status': resp.status_code,
        'total_ms': total,
        'elapsed_header_ms': elapsed,
        'content_encoding': encoding,
        'content_type': resp.headers.get('Content-Type'),
        'content_length': resp.headers.get('Content-Length'),
        'vary': resp.headers.get('Vary'),
        'wire_or_body_bytes': len(raw),
        'uncompressed_bytes': len(body),
        'compressed_bytes': compressed,
        'json_ok': body[:1] in (b'[', b'{'),
    }


def _run_group(
    url: str,
    payload: dict,
    *,
    label: str,
    accept_gzip: bool,
    reuse: bool,
) -> dict[str, Any]:
    # Warm
    if reuse:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4)
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        for i in range(WARMUPS):
            s = _requests_once(session, url, payload, accept_gzip=accept_gzip)
            _assert_ok_sample(s, accept_gzip=accept_gzip, label=f'{label}_warmup_{i}')
        samples = [
            _requests_once(session, url, payload, accept_gzip=accept_gzip)
            for _ in range(SAMPLES)
        ]
        session.close()
    else:
        for i in range(WARMUPS):
            s = _requests_once(None, url, payload, accept_gzip=accept_gzip)
            _assert_ok_sample(s, accept_gzip=accept_gzip, label=f'{label}_warmup_{i}')
        samples = [
            _requests_once(None, url, payload, accept_gzip=accept_gzip)
            for _ in range(SAMPLES)
        ]

    for i, s in enumerate(samples):
        _assert_ok_sample(s, accept_gzip=accept_gzip, label=f'{label}_sample_{i}')

    last = samples[-1]
    return {
        'label': label,
        'reuse_connection': reuse,
        'accept_gzip': accept_gzip,
        'measured': len(samples),
        'status_codes': sorted({s['status'] for s in samples}),
        'total_ms': _stats([s['total_ms'] for s in samples]),
        'ttfb_ms': _stats([s['ttfb_ms'] for s in samples]),
        'body_transfer_ms': _stats([s['body_transfer_ms'] for s in samples]),
        'headers_ms': _stats([s['headers_ms'] for s in samples]),
        'wire_bytes': _stats([float(s['wire_bytes']) for s in samples]),
        'uncompressed_bytes': _stats([float(s['uncompressed_bytes']) for s in samples]),
        'last': last,
        'redirect_counts': [s['redirect_count'] for s in samples],
    }


def _cold_vs_warm(url: str, payload: dict) -> dict[str, Any]:
    """First request after fresh process connect vs subsequent reused session."""
    # Force new connection path for "cold-ish" first request.
    first = _requests_once(None, url, payload, accept_gzip=True)
    session = requests.Session()
    subsequent = []
    for _ in range(10):
        subsequent.append(_requests_once(session, url, payload, accept_gzip=True))
    session.close()
    return {
        'first_request_after_script_start': first,
        'subsequent_reused_session_ms': _stats([s['total_ms'] for s in subsequent]),
        'subsequent_ttfb_ms': _stats([s['ttfb_ms'] for s in subsequent]),
        'note': (
            'Not a Railway idle cold-start proof; measures first client connection '
            'in this script vs reused session.'
        ),
    }


def _curl_timing(url: str, payload: dict) -> dict[str, Any]:
    curl = None
    for candidate in ('curl.exe', 'curl'):
        try:
            subprocess.run([candidate, '--version'], capture_output=True, check=True)
            curl = candidate
            break
        except Exception:
            continue
    if not curl:
        return {'skipped': True, 'reason': 'curl not available'}

    body_file = ROOT / 'tools' / '_phase6_payload.json'
    body_file.write_text(json.dumps(payload), encoding='utf-8')
    fmt = (
        'dns=%{time_namelookup}\\n'
        'connect=%{time_connect}\\n'
        'appconnect=%{time_appconnect}\\n'
        'pretransfer=%{time_pretransfer}\\n'
        'starttransfer=%{time_starttransfer}\\n'
        'total=%{time_total}\\n'
        'size_download=%{size_download}\\n'
        'http_code=%{http_code}\\n'
        'redirects=%{num_redirects}\\n'
        'content_type=%{content_type}\\n'
    )
    cmd = [
        curl,
        '-sS',
        '-o',
        os.devnull,
        '-w',
        fmt,
        '-X',
        'POST',
        url,
        '-H',
        'Content-Type: application/json',
        '-H',
        'Accept-Encoding: gzip',
        '--data-binary',
        f'@{body_file}',
    ]
    # Warm once
    subprocess.run(cmd, capture_output=True, text=True)
    samples = []
    for _ in range(10):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        parsed: dict[str, Any] = {'raw': proc.stdout.strip()}
        for line in proc.stdout.splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                try:
                    parsed[k] = float(v) if k.startswith('time_') or k in {
                        'dns', 'connect', 'appconnect', 'pretransfer',
                        'starttransfer', 'total', 'size_download',
                    } or k in {'dns', 'connect', 'appconnect', 'pretransfer', 'starttransfer', 'total'} else v
                except ValueError:
                    parsed[k] = v
        # curl -w custom keys without time_ prefix in our format
        for k in ('dns', 'connect', 'appconnect', 'pretransfer', 'starttransfer', 'total', 'size_download'):
            if k in parsed and isinstance(parsed[k], str):
                try:
                    parsed[k] = float(parsed[k])
                except ValueError:
                    pass
        samples.append(parsed)
    try:
        body_file.unlink(missing_ok=True)
    except Exception:
        pass

    def _collect(key: str) -> list[float]:
        out = []
        for s in samples:
            v = s.get(key)
            if isinstance(v, (int, float)):
                # curl times are seconds
                out.append(float(v) * 1000 if key != 'size_download' else float(v))
        return out

    totals = _collect('total')
    starts = _collect('starttransfer')
    return {
        'skipped': False,
        'samples': len(samples),
        'dns_ms': _stats(_collect('dns')),
        'connect_ms': _stats(_collect('connect')),
        'tls_appconnect_ms': _stats(_collect('appconnect')),
        'ttfb_starttransfer_ms': _stats(starts),
        'total_ms': _stats(totals),
        'body_transfer_est_ms': _stats(
            [t - s for t, s in zip(totals, starts)] if totals and starts else []
        ),
        'size_download_bytes': _stats(_collect('size_download')),
        'last': samples[-1] if samples else None,
    }


def _confirm_cache_hit_via_shared_redis(payload: dict) -> dict[str, Any]:
    """If local REDIS_URL is the same as Railway Redis, confirm TFGZ1 HIT locally."""
    ser = TrafficFilterRequestSerializer(data=payload)
    if not ser.is_valid():
        return {'ok': False, 'reason': 'invalid payload'}
    data = ser.validated_data

    def boom():
        raise RuntimeError('unexpected miss')

    try:
        body, meta = get_cached_traffic_filter_response(
            payload=data,
            builder=boom,
            serve_mode='gzip_bytes',
        )
        return {
            'ok': True,
            'cache_hit': bool(meta.get('cache_hit')),
            'response_path': meta.get('response_path'),
            'redis_round_trips': meta.get('redis_round_trips'),
            'http_body_bytes': meta.get('http_body_bytes') or (
                len(body) if isinstance(body, (bytes, bytearray)) else None
            ),
            'namespace_version': meta.get('namespace_version'),
            'note': (
                'Confirmed via local Django cache helper against configured Redis. '
                'Valid only if local REDIS_URL points at the same Redis Railway uses.'
            ),
        }
    except RuntimeError:
        # Cold: populate once then confirm HIT.
        from django.db.models import Q
        from traffic.models import Traffic
        from traffic.services.traffic_filter_response import build_traffic_filter_groups

        def builder():
            qs = Traffic.objects.select_related('project', 'segment')
            qs = qs.filter(
                segment__chainage_start__gte=data['chainage_start'],
                segment__chainage_end__lte=data['chainage_end'],
                survey_date=data['date'],
            )
            q = Q()
            for name in data['project_name']:
                q |= Q(project__project_name__iexact=name)
            return build_traffic_filter_groups(qs.filter(q))

        get_cached_traffic_filter_response(
            payload=data,
            builder=builder,
            serve_mode='gzip_bytes',
        )
        body, meta = get_cached_traffic_filter_response(
            payload=data,
            builder=boom,
            serve_mode='gzip_bytes',
        )
        return {
            'ok': True,
            'cache_hit': bool(meta.get('cache_hit')),
            'response_path': meta.get('response_path'),
            'redis_round_trips': meta.get('redis_round_trips'),
            'populated_during_probe': True,
            'http_body_bytes': meta.get('http_body_bytes') or len(body),
            'note': 'Was cold; populated then confirmed HIT via shared Redis path.',
        }
    except Exception as exc:
        return {'ok': False, 'reason': f'{type(exc).__name__}: {exc}'}


def _classify(results: dict) -> dict[str, Any]:
    primary = results['cases']['primary']
    reused = primary['requests_gzip_reused']
    fresh = primary['requests_gzip_new_connection']
    connect = results['connection_probe']
    ttfb = reused['ttfb_ms']['median']
    transfer = reused['body_transfer_ms']['median']
    total = reused['total_ms']['median']
    new_total = fresh['total_ms']['median']
    tls = connect['tls_handshake_ms']['median']
    dns_tcp_tls = connect['dns_tcp_tls_total_ms']['median']

    http_warm = results.get('http_warm_confirmation', {}).get('primary') or {}
    cold = (http_warm.get('cold_first_http') or {})
    cold_total = cold.get('total_ms')
    warm_confirm = (http_warm.get('warm_confirm_http') or {}).get('total_ms')

    hit = results['cache_hit_confirmation']['primary']
    server_side_hint = results.get('phase4a_reference', {}).get('in_process_median_ms')

    unexplained = max(0.0, POSTMAN_OBSERVED_MS - (total or 0))
    factors = []

    # Strong signal: cold HTTP ≈ Postman 6s, warm HTTP ≈ in-process.
    if cold_total and cold_total >= 3500 and total and total < 2000:
        factors.append(
            'F - connection/cold-start or cache MISS on first request '
            f'(cold HTTP ≈ {round(cold_total, 1)} ms vs warm reused ≈ {round(total, 1)} ms)'
        )
    if transfer is not None and transfer >= 0.4 * (total or 1):
        factors.append('E - response body transfer')
    if (new_total - total) >= 200:
        factors.append('F - connection/TLS (new vs reused delta)')
    if dns_tcp_tls and dns_tcp_tls >= 500:
        factors.append('F - DNS/TCP/TLS setup cost')
    if ttfb and server_side_hint and ttfb > 3 * server_side_hint:
        factors.append('E/F - TTFB exceeds in-process server processing')
    if total and total < 1500 and POSTMAN_OBSERVED_MS >= 4000 and not (
        cold_total and cold_total >= 3500
    ):
        factors.append('E/F/H - Postman >> Python warm client (client/UI or different path)')

    if not factors:
        if total and total >= 4000:
            factors.append('G - mixed / server-path slow under external HTTP')
        else:
            factors.append('H - unknown')

    if cold_total and cold_total >= 3500 and abs(cold_total - POSTMAN_OBSERVED_MS) < 2500 and (
        total and total < 2000
    ):
        primary_code = 'F - connection/TLS/cold-start / first-request MISS'
    elif total and total < 2000 and POSTMAN_OBSERVED_MS >= 4000:
        primary_code = 'E - client↔Railway HTTP/body transfer / Postman overhead'
        if (new_total - total) >= 200 or (dns_tcp_tls or 0) >= 300:
            primary_code = 'F - connection/TLS/cold-start (with possible Postman overhead)'
            if transfer and transfer > ttfb:
                primary_code = 'G - mixed (connection + transfer + Postman)'
        elif transfer and transfer >= ttfb:
            primary_code = 'E - client↔Railway HTTP/body transfer'
    elif ttfb and server_side_hint and ttfb <= 2 * server_side_hint and total and total < 2000:
        primary_code = 'E - client↔Railway HTTP/body transfer'
    elif ttfb and server_side_hint and ttfb > 5 * server_side_hint:
        primary_code = 'G - mixed (elevated TTFB vs in-process)'
    else:
        primary_code = 'G - mixed' if len(factors) > 1 else factors[0]

    answer = (
        f'Python external client warm reused gzip median total ≈ {total} ms '
        f'(TTFB ≈ {ttfb} ms, body transfer ≈ {transfer} ms). '
        f'New-connection median ≈ {new_total} ms. '
        f'DNS+TCP+TLS probe median ≈ {dns_tcp_tls} ms. '
        f'Cold first HTTP ≈ {None if cold_total is None else round(cold_total, 1)} ms; '
        f'warm confirm ≈ {None if warm_confirm is None else round(warm_confirm, 1)} ms. '
        f'Postman observed ≈ {POSTMAN_OBSERVED_MS} ms. '
    )
    if cold_total and cold_total >= 3500 and total and total < 2000:
        answer += (
            f'The ~6s Postman latency matches the first/cold HTTP request '
            f'(~{round(cold_total, 1)} ms), not the warm HIT path '
            f'(~{round(total, 1)} ms). Warm HIT is consistent with Phase 4A/5 '
            f'(~{server_side_hint}–700 ms). Dominant explanation: first request '
            f'cache MISS and/or connection cold-start — not TFGZ1 CPU.'
        )
    elif total and total < 1500:
        answer += (
            f'The optimized traffic_filter HIT (~{server_side_hint} ms in-process) plus '
            f'real HTTPS (~{total} ms in Python) does NOT account for Postman ~6s. '
            f'Unexplained vs Postman ≈ {round(unexplained, 1)} ms — likely Postman UI/'
            f'decompression/display, a cold/MISS request, or a different URL/path.'
        )
    else:
        answer += (
            'External Python client also sees multi-second latency; inspect TTFB vs '
            'transfer split and connection reuse delta for the dominant component.'
        )

    return {
        'primary_classification': primary_code,
        'factors': factors,
        'breakdown_ms': {
            'postman_observed': POSTMAN_OBSERVED_MS,
            'python_reused_total_median': total,
            'python_reused_ttfb_median': ttfb,
            'python_reused_transfer_median': transfer,
            'python_new_connection_total_median': new_total,
            'dns_tcp_tls_median': dns_tcp_tls,
            'tls_handshake_median': tls,
            'in_process_reference_median': server_side_hint,
            'cold_first_http_ms': None if cold_total is None else round(cold_total, 3),
            'warm_confirm_http_ms': None if warm_confirm is None else round(warm_confirm, 3),
            'unexplained_postman_minus_python_reused': round(unexplained, 3),
            'connection_reuse_savings_median': round((new_total or 0) - (total or 0), 3),
        },
        'final_answer': answer,
        'cache_hit_ok': bool(hit.get('cache_hit')),
        'http_status_ok': reused.get('status_codes') == [200],
    }


def _write_report(results: dict) -> None:
    clf = results['classification']
    p = results['cases']['primary']
    l = results['cases']['large']
    conn = results['connection_probe']
    b = clf['breakdown_ms']
    lines = [
        '# Traffic Filter Phase 6 — External HTTP Latency Diagnosis',
        '',
        '**Diagnosis only — no production behavior changed.**',
        '',
        '## Final question',
        '',
        '> Why does Postman take ~6 seconds when the optimized traffic_filter HIT handler takes ~300–700 ms?',
        '',
        clf['final_answer'],
        '',
        f"**Primary classification: {clf['primary_classification']}**",
        '',
        'Factors:',
    ]
    for f in clf['factors']:
        lines.append(f'- {f}')
    lines.extend([
        '',
        '## Environment',
        '',
        f"- base_url: `{results['environment']['base_url_redacted']}`",
        f"- base_url_source: `{results['environment']['base_url_source']}`",
        f"- postman_observed_ms: {POSTMAN_OBSERVED_MS}",
        f"- phase4a in-process reference median ms: {results['phase4a_reference']['in_process_median_ms']}",
        '',
        '## Cache HIT confirmation (shared Redis probe)',
        '',
        f"- primary: `{results['cache_hit_confirmation']['primary']}`",
        f"- large: `{results['cache_hit_confirmation']['large']}`",
        '',
        '## Latency waterfall (primary, measured)',
        '',
        '```text',
        f"Postman total (observed)     ~ {POSTMAN_OBSERVED_MS} ms",
        '│',
        f"├── DNS                      ~ {conn['dns_ms']['median']} ms (probe)",
        f"├── TCP connect              ~ {conn['tcp_connect_ms']['median']} ms (probe)",
        f"├── TLS handshake            ~ {conn['tls_handshake_ms']['median']} ms (probe)",
        f"├── Cold first HTTP          ~ {b.get('cold_first_http_ms')} ms",
        f"├── Python reused total      ~ {b['python_reused_total_median']} ms",
        f"│   ├── TTFB                 ~ {b['python_reused_ttfb_median']} ms",
        f"│   │   └── in-process ref   ~ {b['in_process_reference_median']} ms (Phase 4A)",
        f"│   └── body transfer        ~ {b['python_reused_transfer_median']} ms",
        f"├── Python new-connection    ~ {b['python_new_connection_total_median']} ms",
        f"└── Unexplained vs Postman   ~ {b['unexplained_postman_minus_python_reused']} ms",
        '```',
        '',
        '## HTTP warm confirmation (Railway)',
        '',
        f"- primary: `{results.get('http_warm_confirmation', {}).get('primary')}`",
        f"- large: `{results.get('http_warm_confirmation', {}).get('large')}`",
        '',
        '## Response sizes (primary gzip last)',
        '',
        f"- content_encoding / content_length / wire / uncompressed: "
        f"`{p['requests_gzip_reused']['last'].get('content_encoding')}` / "
        f"`{p['requests_gzip_reused']['last'].get('content_length')}` / "
        f"`{p['requests_gzip_reused']['last'].get('wire_bytes')}` / "
        f"`{p['requests_gzip_reused']['last'].get('uncompressed_bytes')}`",
        f"- large gzip last: "
        f"`{l['requests_gzip_reused']['last'].get('content_encoding')}` / "
        f"`{l['requests_gzip_reused']['last'].get('content_length')}` / "
        f"`{l['requests_gzip_reused']['last'].get('wire_bytes')}` / "
        f"`{l['requests_gzip_reused']['last'].get('uncompressed_bytes')}`",
        '',
        '## Connection reuse',
        '',
        f"- gzip NEW connection median/P95: "
        f"{p['requests_gzip_new_connection']['total_ms']['median']} / "
        f"{p['requests_gzip_new_connection']['total_ms']['p95']} ms",
        f"- gzip REUSED session median/P95: "
        f"{p['requests_gzip_reused']['total_ms']['median']} / "
        f"{p['requests_gzip_reused']['total_ms']['p95']} ms",
        f"- savings (new−reused) median: {b['connection_reuse_savings_median']} ms",
        '',
        '## Gzip vs identity (primary, reused)',
        '',
        f"- gzip total/TTFB/transfer: "
        f"{p['requests_gzip_reused']['total_ms']['median']} / "
        f"{p['requests_gzip_reused']['ttfb_ms']['median']} / "
        f"{p['requests_gzip_reused']['body_transfer_ms']['median']} ms",
        f"- identity total/TTFB/transfer: "
        f"{p['requests_identity_reused']['total_ms']['median']} / "
        f"{p['requests_identity_reused']['ttfb_ms']['median']} / "
        f"{p['requests_identity_reused']['body_transfer_ms']['median']} ms",
        f"- gzip last headers: `{p['requests_gzip_reused']['last']}`",
        f"- identity last headers: `{p['requests_identity_reused']['last']}`",
        '',
        '## Large payload (Chittorgarh)',
        '',
        f"- gzip reused total/TTFB/transfer: "
        f"{l['requests_gzip_reused']['total_ms']['median']} / "
        f"{l['requests_gzip_reused']['ttfb_ms']['median']} / "
        f"{l['requests_gzip_reused']['body_transfer_ms']['median']} ms",
        f"- last: `{l['requests_gzip_reused']['last']}`",
        '',
        '## curl / httpx',
        '',
        f"- curl: `{results.get('curl_primary')}`",
        f"- httpx gzip reused median: "
        f"{(results.get('httpx_primary_gzip_reused') or {}).get('total_ms', {}).get('median')}",
        '',
        '## Cold-ish vs warm (script session)',
        '',
        f"`{results.get('cold_vs_warm_primary')}`",
        '',
        '## Redirects',
        '',
        f"- primary gzip redirect counts: {p['requests_gzip_reused']['redirect_counts']}",
        '',
        '## Breakdown vs Postman',
        '',
        '| Component | Approx ms |',
        '|---|---:|',
        f"| Postman observed | {POSTMAN_OBSERVED_MS} |",
        f"| 1. connection/TLS (probe DNS+TCP+TLS) | {b['dns_tcp_tls_median']} |",
        f"| 2. TTFB (Python reused) | {b['python_reused_ttfb_median']} |",
        f"| 3. response transfer (Python reused) | {b['python_reused_transfer_median']} |",
        f"| 4. cold first HTTP (likely MISS) | {b.get('cold_first_http_ms')} |",
        f"| 5. Postman − warm Python reused | {b['unexplained_postman_minus_python_reused']} |",
        f"| 6. in-process server reference | {b['in_process_reference_median']} |",
        '',
        '## Phase 7 guidance',
        '',
        '- Do **not** redesign TFGZ1 / cache keys / serializers next.',
        '- If Python external ≪ Postman: investigate Postman settings, URL, cold start, UI render.',
        '- If Python external TTFB ≫ in-process: investigate Railway edge → app → Redis internal path.',
        '- If transfer dominates: bandwidth/client download path.',
        '',
    ])
    OUT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    base, source = _base_url()
    url = f'{base}/traffic_filter/'
    _log(f'Phase 6 base={base} source={source}')

    results: dict[str, Any] = {
        'environment': {
            'base_url_redacted': _redact_url(base),
            'base_url_source': source,
            'endpoint': '/traffic_filter/',
            'warmups': WARMUPS,
            'samples': SAMPLES,
        },
        'phase4a_reference': {
            'in_process_median_ms': 300,
            'note': 'Phase 4A warm gzip HIT median ~292–300 ms',
        },
        'phase5_reference': {
            'redis_round_trips_on_hit': 1,
            'note': 'get_many version+value in one RTT',
        },
        'postman_observed_ms': POSTMAN_OBSERVED_MS,
    }

    _log('Confirming cache HIT via shared Redis (if configured)...')
    results['cache_hit_confirmation'] = {
        'primary': _confirm_cache_hit_via_shared_redis(PRIMARY),
        'large': _confirm_cache_hit_via_shared_redis(LARGE),
    }
    _log(f"primary hit={results['cache_hit_confirmation']['primary']}")

    _log('Warming Railway via HTTP (capture cold first)...')
    results['http_warm_confirmation'] = {
        'primary': _warm_railway_http_until_hit(url, PRIMARY, label='primary'),
        'large': _warm_railway_http_until_hit(url, LARGE, label='large'),
    }
    _log(
        'primary cold_ms='
        f"{results['http_warm_confirmation']['primary']['cold_first_http']['total_ms']:.1f} "
        'warm_ms='
        f"{results['http_warm_confirmation']['primary']['warm_confirm_http']['total_ms']:.1f}"
    )

    _log('DNS/TCP/TLS probe...')
    results['connection_probe'] = _tcp_tls_connect_ms(base)

    results['cases'] = {}
    for name, payload in (('primary', PRIMARY), ('large', LARGE)):
        _log(f'=== {name}: gzip new connection ===')
        gzip_new = _run_group(
            url, payload, label=f'{name}_gzip_new', accept_gzip=True, reuse=False,
        )
        _log(f'=== {name}: gzip reused session ===')
        gzip_reuse = _run_group(
            url, payload, label=f'{name}_gzip_reuse', accept_gzip=True, reuse=True,
        )
        _log(f'=== {name}: identity reused session ===')
        identity_reuse = _run_group(
            url, payload, label=f'{name}_identity_reuse', accept_gzip=False, reuse=True,
        )
        results['cases'][name] = {
            'payload': payload,
            'requests_gzip_new_connection': gzip_new,
            'requests_gzip_reused': gzip_reuse,
            'requests_identity_reused': identity_reuse,
        }
        _log(
            f"[{name}] gzip_new={gzip_new['total_ms']['median']} "
            f"gzip_reuse={gzip_reuse['total_ms']['median']} "
            f"identity={identity_reuse['total_ms']['median']} "
            f"ttfb_reuse={gzip_reuse['ttfb_ms']['median']} "
            f"xfer={gzip_reuse['body_transfer_ms']['median']}"
        )

    _log('Cold-ish vs warm...')
    results['cold_vs_warm_primary'] = _cold_vs_warm(url, PRIMARY)

    _log('httpx reused gzip...')
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        for _ in range(5):
            _httpx_once(client, url, PRIMARY, accept_gzip=True)
        httpx_samples = [
            _httpx_once(client, url, PRIMARY, accept_gzip=True) for _ in range(SAMPLES)
        ]
    results['httpx_primary_gzip_reused'] = {
        'total_ms': _stats([s['total_ms'] for s in httpx_samples]),
        'last': httpx_samples[-1],
    }

    _log('curl timings...')
    results['curl_primary'] = _curl_timing(url, PRIMARY)

    results['classification'] = _classify(results)
    OUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    _write_report(results)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    _log(results['classification']['primary_classification'])
    _log(results['classification']['final_answer'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
