import os,re,sys,json
sys.path.insert(0,'/app/nas')
import psycopg
from catalog import write
APPLY = '--apply' in sys.argv
url = os.environ['DATABASE_URL']
pat = re.compile(r'^(\d+)\s*\((\d+\s*P\s*\+\s*\d+\s*E)\)$')
backup=[]
with psycopg.connect(url) as conn:
    rows = conn.execute("SELECT entity_id,data FROM catalog_entities WHERE entity='product' AND deleted_at IS NULL AND data->>'category_id'='cpu' FOR UPDATE").fetchall()
    for pid,data in rows:
        specs=data.get('specs',{}); m=pat.match(str(specs.get('Núcleos','')))
        if not m: continue
        if 'Distribución de núcleos' in specs: print('skip',pid); continue
        backup.append(dict(id=pid,before=data))
        new={**data,'specs':{**specs,'Núcleos':m.group(1),'Distribución de núcleos':re.sub(r'\s*','',m.group(2)).replace('P+',' P + ').replace('E',' E').replace('  ',' ').strip()}}
        print(pid, specs['Núcleos'],'->',new['specs']['Núcleos'],'|',new['specs']['Distribución de núcleos'])
        if APPLY: write(conn,'product',pid,new)
    if not APPLY: conn.rollback()
print(json.dumps(backup,ensure_ascii=False),file=open('/tmp/cpu-cores-backup.json','w') if APPLY else open(os.devnull,'w'))
