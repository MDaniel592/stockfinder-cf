"""Stage source fixtures in the NAS review queue without publishing observations."""
import json
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
for file in sorted((root/'data/retailers').glob('*candidates.json')):
    records=json.loads(file.read_text())
    command=['docker','compose','--env-file','.env','-f','infra/compose/compose.yaml','run','--rm','-T',
             '-v',str(root/'data/retailers')+':/input:ro','sync-agent','python','nas/review_queue.py','stage','--file','/input/'+file.name]
    result=subprocess.run(command,cwd=root,text=True,capture_output=True,check=True)
    response=json.loads(result.stdout)
    print(json.dumps({'source':file.name,'candidates':len(records),'pending_ids':len(set(response['review_ids']))}))
