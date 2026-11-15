"""Vista previa local: copia el historial del outbox de PostgreSQL a un Worker local.

Dos pasos, para no necesitar psycopg en el host ni red especial en Docker:
  export  (dentro del contenedor sync-agent, SOLO LECTURA)  -> JSON con todos los eventos
  push    (en el host, solo stdlib)                          -> POST firmado a http://127.0.0.1:PUERTO/internal/sync

Nunca escribe en PostgreSQL ni marca eventos como confirmados, y solo acepta
destinos locales o el Worker de STAGING (stockfinder-staging, datos en Supabase): no puede enviar nada a producción.
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

MAX_BODY = 60_000
# Único destino remoto admitido: el Worker de staging (scripts/staging_replay.sh --remote).
STAGING_HOSTS = ('stockfinder-staging.stockfinder.workers.dev',)


def export(out_path):
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL'], options='-c default_transaction_read_only=on') as conn:
        # Estado actual: el último evento de cada entidad (el historial antiguo puede no cumplir el contrato vigente)
        rows = conn.execute('''SELECT event_id,entity,entity_id,version,operation,changed_at,data FROM (
                                 SELECT DISTINCT ON (entity, entity_id) * FROM catalog_outbox
                                 ORDER BY entity, entity_id, event_id DESC) latest
                               ORDER BY event_id''').fetchall()
    events = []
    for row in rows:
        e = dict(zip(['event_id', 'entity', 'entity_id', 'version', 'operation', 'changed_at', 'data'], row))
        e['changed_at'] = e['changed_at'].isoformat(timespec='milliseconds')
        if e['operation'] == 'delete':
            e.pop('data', None)
        events.append(e)
    tmp = out_path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(events, fh, ensure_ascii=False, separators=(',', ':'), default=str)
    os.replace(tmp, out_path)
    print(json.dumps(dict(event='local_export', events=len(events), file=out_path)))


def _post(endpoint, secret, events):
    body = json.dumps(dict(batch_id=str(uuid.uuid4()), schema_version=1, events=events), separators=(',', ':')).encode()
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    sig = hmac.new(secret.encode(), ts.encode() + b'.' + nonce.encode() + b'.' + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(endpoint, data=body, method='POST', headers={
        # User-Agent explícito: Cloudflare responde 403 al de urllib por defecto (destino remoto de staging).
        'User-Agent': 'stockfinder-replay/1', 'Content-Type': 'application/json', 'X-Sync-Timestamp': ts, 'X-Sync-Nonce': nonce, 'X-Sync-Signature': sig})
    with urllib.request.urlopen(req, timeout=60) as resp:
        ack = json.load(resp)
    if sorted(ack.get('acknowledged_event_ids', [])) != sorted(e['event_id'] for e in events):
        raise RuntimeError('acuse de recibo inesperado')


def _batches(events, size):
    batch = []
    for e in events:
        candidate = batch + [e]
        if len(candidate) > size or len(json.dumps(candidate, ensure_ascii=False)) > MAX_BODY:
            if batch:
                yield batch
            batch = [e]
        else:
            batch = candidate
    if batch:
        yield batch


def push(in_path, endpoint, secret, size):
    host, scheme = urlparse(endpoint).hostname, urlparse(endpoint).scheme
    local = host in ('127.0.0.1', 'localhost') and scheme == 'http'
    staging = host in STAGING_HOSTS and scheme == 'https'
    if not (local or staging):
        sys.exit('Destino no permitido: solo la vista previa en 127.0.0.1 o el Worker de staging')
    with open(in_path) as fh:
        events = json.load(fh)
    sent, skipped = 0, []
    for batch in _batches(events, size):
        try:
            _post(endpoint, secret, batch)
            sent += len(batch)
        except urllib.error.HTTPError as err:
            if err.code != 400:
                raise
            for event in batch:  # aislar el evento que incumple el contrato y seguir
                try:
                    _post(endpoint, secret, [event])
                    sent += 1
                except urllib.error.HTTPError as one:
                    if one.code != 400:
                        raise
                    skipped.append(f"{event['entity']}:{event['entity_id']}")
    print(json.dumps(dict(event='local_replay', events=sent, skipped=len(skipped), skipped_ids=skipped[:20])))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('export'); e.add_argument('--out', required=True)
    s = sub.add_parser('push'); s.add_argument('--in', dest='inp', required=True)
    s.add_argument('--endpoint', required=True); s.add_argument('--batch', type=int, default=20)
    a = p.parse_args()
    if a.cmd == 'export':
        export(a.out)
    else:
        push(a.inp, a.endpoint, os.environ['LOCAL_SYNC_SECRET'], a.batch)
