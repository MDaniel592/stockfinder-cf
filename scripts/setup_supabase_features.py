"""Provision the server-only feature facade and least-privilege replica credentials. No secrets on stdout."""
import os
import secrets
from pathlib import Path
from supabase import api, sql, quote, REF, POOLER, ROOT, migrate

def secret_file(path, content):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as fh: fh.write(content)
    os.chmod(path,0o600)

def main():
    migrate()
    path=ROOT/'.env.supabase-replica.local'
    if not path.exists():
        password=secrets.token_urlsafe(32)
        sql('ALTER ROLE stockfinder_replica LOGIN PASSWORD '+quote(password)+"; ALTER ROLE stockfinder_replica SET statement_timeout='30s';")
        secret_file(path,f'SUPABASE_REPLICA_URL=postgres://stockfinder_replica.{REF}:{password}@{POOLER}:5432/postgres?sslmode=require\n')
    keys=api('GET',f'projects/{REF}/api-keys')
    key=next((k.get('api_key') for k in keys if k.get('name')=='service_role'),None)
    if not key: raise SystemExit('No server API key available; no key was printed.')
    secret_file(ROOT/'.env.supabase-features.local','FEATURES_KEY='+key+'\n')
    config=api('GET',f'projects/{REF}/postgrest') or {}
    if config.get('db_schema','') not in ('','stockfinder_features'):
        raise SystemExit('Unexpected exposed schemas; no API configuration was changed.')
    api('PATCH',f'projects/{REF}/postgrest',{'db_schema':'stockfinder_features'})
    # Check the facade is the only permission granted to the API role.
    rows=sql("SELECT has_schema_privilege('anon','stockfinder_features','USAGE') AS anon_access, has_schema_privilege('authenticated','stockfinder_features','USAGE') AS user_access, has_table_privilege('service_role','stockfinder_features.snapshot','SELECT') AS direct_read, has_function_privilege('service_role','stockfinder_features.rpc(text,jsonb)','EXECUTE') AS rpc_access")
    assert rows==[{'anon_access':False,'user_access':False,'direct_read':False,'rpc_access':True}], 'Unexpected feature privileges'
    print('Supabase features ready: server-only RPC, no anonymous access or direct table read; credentials stored in ignored 0600 files.')

if __name__=='__main__': main()
