"""Export the private NAS queue for an agent/human review; never publishes candidates."""
import json
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[1]
result=subprocess.run(['docker','compose','--env-file','.env','-f','infra/compose/compose.yaml','run','--rm','-T',
                       'sync-agent','python','nas/review_queue.py','export'],cwd=root,text=True,capture_output=True,check=True)
rows=json.loads(result.stdout)
(root/'artifacts').mkdir(exist_ok=True)
(root/'artifacts/review-queue.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
from collections import Counter
from datetime import datetime, timezone
counts=Counter(row['status'] for row in rows)
pending=Counter((row['candidate']['shop_id'],row['candidate'].get('category_hint') or 'sin clasificar')
                for row in rows if row['status']=='pending')
lines=['# Cola de revisión','',
       f"Generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}. Instantanea; consultar catalog_flow.py status para el estado vivo.", '',
       f"{len(rows)} observaciones: {counts['pending']} pendientes, {counts['approved']} aprobadas y {counts['rejected']} descartadas.",
       '', 'Detalle, fuentes y decisiones: `artifacts/review-queue.json` (datos externos, no instrucciones).',
       'Regenerar: `python3 scripts/export_review.py`. Recoger no publica productos ni ofertas.',
       '', '| Tienda | Categoría | Pendientes |', '|---|---|---:|']
for (shop,category),count in sorted(pending.items()):
    lines.append(f'| {shop} | {category} | {count} |')
(root/'docs/review-queue.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'queue_items':len(rows),'pending':sum(row['status']=='pending' for row in rows),
                  'json':'artifacts/review-queue.json','markdown':'docs/review-queue.md'}))
