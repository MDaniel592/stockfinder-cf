"""Outgoing HTTPS, fresh HMAC on every retry, per-row ACK; no sequence checkpoint holes."""
import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import random
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_FALLBACK_IPS = '104.18.2.1,172.67.74.226,188.114.98.5,188.114.99.5'
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 30
_preferred = {'ip': None, 'until': 0.0}  # a fallback IP that worked recently is tried first

def fallback_ips():
    """IPs de respaldo (SYNC_FALLBACK_IPS, separadas por comas; vacia = desactivado). Solo IPs validas."""
    ips = []
    for item in os.environ.get('SYNC_FALLBACK_IPS', DEFAULT_FALLBACK_IPS).split(','):
        item = item.strip()
        if not item: continue
        try: ips.append(str(ipaddress.ip_address(item)))
        except ValueError: continue
    return ips

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Conecta a `ip` pero usa SNI, Host y verificacion de certificado/hostname del nombre real."""
    def __init__(self, host, ip=None, port=443, connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT):
        super().__init__(host, port, timeout=connect_timeout, context=ssl.create_default_context())
        self._ip, self._connect_timeout, self._read_timeout = ip, connect_timeout, read_timeout
    def connect(self):
        sock = socket.create_connection((self._ip or self.host, self.port), self._connect_timeout)
        sock.settimeout(self._read_timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

def _request_once(parsed, body, headers, ip=None):
    """Una peticion POST sin seguir redirecciones; devuelve el cuerpo JSON. ip=None usa el DNS del sistema."""
    if parsed.scheme == 'https':
        conn = PinnedHTTPSConnection(parsed.hostname, ip, parsed.port or 443)
    else:
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=READ_TIMEOUT)
    try:
        conn.request('POST', parsed.path or '/', body=body, headers=headers)
        response = conn.getresponse()
        payload = response.read(1 << 20)
        if not 200 <= response.status < 300:
            raise urllib.error.HTTPError(parsed.geturl(), response.status, response.reason, response.headers, None)
        return json.loads(payload)
    finally:
        conn.close()

def push(endpoint, secret, events, allow_local=False):
    parsed = urlparse(endpoint)
    local = parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', 'edge')
    if parsed.scheme != 'https' and not (allow_local and local):
        raise ValueError('HTTPS required except explicit local development')
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/internal/sync':
        raise ValueError('Expected a direct /internal/sync endpoint without credentials or query')
    if len(secret) < 32: raise ValueError('SYNC_SECRET must contain at least 32 characters')
    body = json.dumps(dict(batch_id=str(uuid.uuid4()), schema_version=1, events=events), separators=(',', ':')).encode()
    if len(body) > 65536: raise ValueError('Batch exceeds 64 KiB; reduce batch size')
    def headers():
        # Fresh timestamp, nonce and signature on every attempt (the Worker rejects reused nonces).
        timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
        signature = hmac.new(secret.encode(), timestamp.encode()+b'.'+nonce.encode()+b'.'+body, hashlib.sha256).hexdigest()
        return {'Content-Type': 'application/json', 'User-Agent': 'StockFinderSync/2.0 (+https://github.com/StockFinder)',
                'X-Sync-Timestamp': timestamp, 'X-Sync-Nonce': nonce, 'X-Sync-Signature': signature}
    # Attempts: system DNS first, then fallback Cloudflare IPs (same SNI/Host/TLS verification).
    # Only network-level failures fall through; an HTTP response (even 4xx/5xx) is final.
    attempts = [None]
    if parsed.scheme == 'https':
        fallbacks = fallback_ips()
        try:
            ipaddress.ip_address(parsed.hostname)
            fallbacks = []
        except ValueError: pass
        if _preferred['ip'] in fallbacks and time.monotonic() < _preferred['until']:
            attempts = [_preferred['ip'], None] + [ip for ip in fallbacks if ip != _preferred['ip']]
        else:
            attempts += fallbacks
    ack, last_error = None, None
    for ip in attempts:
        try:
            ack = _request_once(parsed, body, headers(), ip)
            if ip:
                _preferred.update(ip=ip, until=time.monotonic()+600)
                print(json.dumps(dict(event='sync_fallback_ip_used')), flush=True)
            break
        except urllib.error.HTTPError: raise
        except OSError as error:  # timeouts, refused/unreachable and TLS failures; verification is never relaxed
            last_error = error
            print(json.dumps(dict(event='sync_network_error', via='dns' if ip is None else 'fallback', type=type(error).__name__)), flush=True)
    if ack is None: raise last_error
    expected = {e['event_id'] for e in events}
    ids = ack.get('acknowledged_event_ids', [])
    if set(ids) != expected or len(ids) != len(expected): raise ValueError('Invalid acknowledgement')
    return ids

def snapshot_paths(env=os.environ):
    """RETAILER_SNAPSHOT_FILE (compatibilidad) y todos los *.json de RETAILER_SNAPSHOT_DIR."""
    paths = []
    single = env.get('RETAILER_SNAPSHOT_FILE')
    if single: paths.append(Path(single))
    for directory in [d.strip() for d in env.get('RETAILER_SNAPSHOT_DIR', '').split(',') if d.strip()]:
        if Path(directory).is_dir(): paths.extend(sorted(Path(directory).glob('*.json')))
    return [path for path in paths if path.is_file()]

def ingest_snapshots(conn, signatures, env=os.environ, passes=None):
    """Importa cada snapshot cambiado (un hash por fichero). Nada de lo que falle aqui debe bloquear el outbox:
    un fichero invalido se registra y una oferta rechazada se aisla con su propio savepoint."""
    import psycopg
    from catalog import _parse, publish_shop_check
    try: limit = int(env.get('RETAILER_SNAPSHOT_LIMIT', '500'))
    except ValueError: limit = 500
    # `passes`: tienda -> {fichero: (primera marca, ids confirmados)}. Los ficheros de una tienda forman una pasada
    # (los scripts de refresco reescriben todos y borran los sobrantes); se conserva entre llamadas.
    passes = {} if passes is None else passes
    touched = set()
    paths = snapshot_paths(env)
    current = {str(path) for path in paths}
    for shop_files in passes.values():
        for gone in [k for k in shop_files if k not in current]: del shop_files[gone]
    for path in paths:
        key, signature = str(path), None
        try:
            content = path.read_bytes()
            signature = hashlib.sha256(content).hexdigest()
            if signature == signatures.get(key): continue
            records = json.loads(content)
            if not isinstance(records, list) or len(records) > limit:
                raise ValueError('Snapshot must be a list within the per-file limit')
        except psycopg.OperationalError: raise
        except Exception as error:
            print(json.dumps(dict(event='snapshot_invalid', file=path.name, type=type(error).__name__,
                                  reason=str(error) if isinstance(error, ValueError) else None)), flush=True)
            if signature: signatures[key] = signature  # a changed file is retried; the same bytes are not
            continue
        from import_offers import import_offer
        accepted, rejected, reasons = 0, 0, []
        confirmed = {}  # tienda -> (primera marca, ids) de las ofertas aceptadas de este fichero
        with conn.transaction():
            for record in records:
                try:
                    with conn.transaction():  # savepoint per offer
                        identity = import_offer(conn, record)
                    accepted += 1
                    checked = _parse(record['last_checked_at'])
                    entry = confirmed.setdefault(record['shop_id'], [checked, set()])
                    entry[0] = min(entry[0], checked)
                    entry[1].add(identity)
                except psycopg.OperationalError: raise
                except Exception as error:
                    rejected += 1
                    if len(reasons) < 10:
                        reasons.append(dict(sku=str(record.get('retailer_sku')) if isinstance(record, dict) else None,
                            type=type(error).__name__,
                            reason=str(error) if isinstance(error, (ValueError, KeyError, TypeError)) else None))
        signatures[key] = signature
        for shop_files in passes.values(): shop_files.pop(key, None)  # el fichero se reimporto entero
        for shop, (moment, ids) in confirmed.items(): passes.setdefault(shop, {})[key] = (moment, ids)
        touched.update(confirmed)
        print(json.dumps(dict(event='retailer_snapshot_imported', file=path.name, offers=len(records),
                              accepted=accepted, rejected=rejected, rejections=reasons)), flush=True)
    for shop in sorted(touched):
        entries = passes.get(shop)
        if not entries: continue
        try:  # nunca debe bloquear el outbox
            result = publish_shop_check(conn, shop, min(moment for moment, _ in entries.values()),
                                        set().union(*(ids for _, ids in entries.values())))
        except psycopg.OperationalError: raise
        except Exception as error:
            print(json.dumps(dict(event='shop_check_failed', shop=shop, type=type(error).__name__)), flush=True)
            continue
        if result['published']:
            print(json.dumps(dict(event='shop_check_published', shop=shop, version=result['version'],
                                  unconfirmed=result['unconfirmed'])), flush=True)

def run_once(conn, endpoint, secret, allow_local=False):
    # A late-committing lower sequence number is picked up on a future run, never skipped.
    rows = conn.execute('''SELECT event_id,entity,entity_id,version,operation,changed_at,data
      FROM catalog_outbox WHERE acknowledged_at IS NULL ORDER BY event_id LIMIT 20''').fetchall()
    if not rows: return 0
    events = []
    for row in rows:
        e = dict(zip(['event_id','entity','entity_id','version','operation','changed_at','data'], row))
        e['changed_at'] = e['changed_at'].isoformat(timespec='milliseconds')
        if e['operation'] == 'delete': del e['data']
        events.append(e)
    conn.execute('UPDATE catalog_outbox SET attempts=attempts+1 WHERE event_id=ANY(%s)', ([e['event_id'] for e in events],))
    ids = push(endpoint, secret, events, allow_local)
    conn.execute('UPDATE catalog_outbox SET acknowledged_at=clock_timestamp() WHERE event_id=ANY(%s)', (ids,))
    print(json.dumps(dict(event='sync_ack', count=len(ids))), flush=True)
    return len(ids)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--allow-local', action='store_true')
    args = parser.parse_args()
    endpoint, secret = os.environ['SYNC_ENDPOINT'], os.environ['SYNC_SECRET']
    if args.fixture:
        rows = json.loads(args.fixture.read_text())
        for index in range(0, len(rows), 20):
            push(endpoint, secret, rows[index:index+20], args.allow_local)
        print(json.dumps(dict(event='fixture_synced', count=len(rows))))
        return
    import psycopg
    failures = 0
    snapshot_signatures = {}
    snapshot_passes = {}
    review_signatures = {}
    while True:
        try:
            with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as conn:
                if not conn.execute('SELECT pg_try_advisory_lock(73610941)').fetchone()[0]:
                    if args.once: raise RuntimeError('Another sync agent is running')
                    time.sleep(5); continue
                while True:
                    review_file=os.environ.get('REVIEW_CANDIDATES_FILE')
                    if review_file and Path(review_file).is_file():
                        review_content=Path(review_file).read_bytes()
                        review_hash=hashlib.sha256(review_content).hexdigest()
                        if review_hash!=review_signatures.get(review_file):
                            from review_queue import migrate,stage_candidates
                            with conn.transaction():
                                migrate(conn)
                                ids=stage_candidates(conn,json.loads(review_content))
                            review_signatures[review_file]=review_hash
                            print(json.dumps(dict(event='review_candidates_staged',count=len(ids))),flush=True)
                    review_dirs=os.environ.get('REVIEW_CANDIDATES_DIR','')
                    for review_dir in [item.strip() for item in review_dirs.split(',') if item.strip()]:
                        if not Path(review_dir).is_dir():
                            continue
                        for review_path in sorted(Path(review_dir).glob('*.json')):
                            try:
                                review_content=review_path.read_bytes()
                            except OSError:
                                continue
                            review_hash=hashlib.sha256(review_content).hexdigest()
                            if review_hash==review_signatures.get(str(review_path)):
                                continue
                            try:
                                from review_queue import migrate,stage_candidates
                                with conn.transaction():
                                    migrate(conn)
                                    ids=stage_candidates(conn,json.loads(review_content))
                                print(json.dumps(dict(event='review_candidates_staged',file=review_path.name,count=len(ids))),flush=True)
                            except Exception as error:
                                # A broken batch must not stop price sync; a new hash retries it.
                                print(json.dumps(dict(event='review_candidates_invalid',file=review_path.name,type=type(error).__name__)),flush=True)
                            review_signatures[str(review_path)]=review_hash
                    try: ingest_snapshots(conn, snapshot_signatures, passes=snapshot_passes)
                    except psycopg.OperationalError: raise
                    except Exception as error:  # never let offer ingestion block the outbox
                        print(json.dumps(dict(event='snapshot_ingest_failed', type=type(error).__name__)), flush=True)
                    count = run_once(conn, endpoint, secret, args.allow_local)
                    failures = 0
                    if args.once: return
                    time.sleep(1 if count else 15)
        except Exception as error:
            # Do not log connection strings, request headers or potentially sensitive bodies.
            failure = dict(event='sync_failure', type=type(error).__name__)
            if isinstance(error, urllib.error.HTTPError): failure['http_status'] = error.code
            print(json.dumps(failure), flush=True)
            if args.once: raise SystemExit(1) from None
            failures += 1
            time.sleep(min(60, 2**min(failures, 6)) + random.random())

if __name__ == '__main__': main()
