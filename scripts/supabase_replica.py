"""Independent incremental NAS -> Supabase replica. Never acknowledges or rewinds the D1 outbox.

The local checkpoint advances only after the remote commit. Replays are idempotent by entity version.
Use --once for a catch-up; otherwise poll every 15 minutes. Credentials only from the environment.
"""
import argparse
import json
import os
import signal
import time
import uuid
import psycopg
from psycopg.types.json import Json

stop = False
def stopping(*_):
    global stop
    stop = True

def replicate():
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as source:
        if not source.execute('SELECT pg_try_advisory_lock(73610945)').fetchone()[0]:
            return
        source.execute('CREATE TABLE IF NOT EXISTS catalog_replica_checkpoints(name text PRIMARY KEY,event_id bigint NOT NULL,updated_at timestamptz NOT NULL DEFAULT now())')
        source.execute("INSERT INTO catalog_replica_checkpoints(name,event_id) VALUES('supabase-features',0) ON CONFLICT DO NOTHING")
        checkpoint = source.execute("SELECT event_id FROM catalog_replica_checkpoints WHERE name='supabase-features'").fetchone()[0]
        sent = 0
        with psycopg.connect(os.environ['SUPABASE_REPLICA_URL'], autocommit=True, connect_timeout=15) as target:
            while not stop:
                rows = source.execute('SELECT event_id,entity,entity_id,version,operation,changed_at,data FROM catalog_outbox WHERE event_id>%s ORDER BY event_id LIMIT 200', (checkpoint,)).fetchall()
                if not rows:
                    break
                events = []
                for row in rows:
                    event = dict(zip(('event_id','entity','entity_id','version','operation','changed_at','data'), row))
                    event['changed_at'] = event['changed_at'].isoformat()
                    if event['operation'] == 'delete':
                        event.pop('data', None)
                    elif event.get('data'):
                        event['data'] = {k:int(v) if isinstance(v,bool) else v for k,v in event['data'].items()}
                    events.append(event)
                target.execute('SELECT stockfinder.feature_replica_apply(%s,%s)', (str(uuid.uuid4()),Json(events))).fetchone()
                checkpoint = rows[-1][0]
                source.execute("UPDATE catalog_replica_checkpoints SET event_id=%s,updated_at=now() WHERE name='supabase-features'", (checkpoint,))
                sent += len(events)
            if not stop:
                target.execute('SELECT stockfinder.feature_refresh()').fetchone()
        print(json.dumps({'event':'supabase_replica','checkpoint':checkpoint,'events':sent,'complete':not stop}),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--interval',type=int,default=900)
    args=parser.parse_args()
    signal.signal(signal.SIGTERM,stopping); signal.signal(signal.SIGINT,stopping)
    while not stop:
        try:
            replicate()
        except Exception as error:
            # Exception text may contain a connection string or data: log the class only.
            print(json.dumps({'event':'supabase_replica_error','type':type(error).__name__}),flush=True)
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            return
        for _ in range(max(10,args.interval)):
            if stop: break
            time.sleep(1)

if __name__=='__main__':
    main()
