"""Rellena huecos de specs por inferencia determinista (NO son datos de ficha). Salida: data/overrides/inferred-<fecha>-<cat>.json
Cada nota empieza por «INFERIDO:». Para retirarlo todo: borrar data/overrides/inferred-*.json.
  python3 scripts/specs_infer.py            # simulación
  python3 scripts/specs_infer.py --write    # escribe los ficheros
"""
import sys,json,re,collections
from pathlib import Path
R=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(R/'scripts')); sys.path.insert(0,str(R/'nas'))
import spec_gaps_web as g, spec_overrides as so
DATE='2026-11-10'
d=g.load(R); P=d['products']
def gaps(p): return {x['key'] for x in p.get('gaps',[])}
def url(p): return p.get('manufacturer_url') if str(p.get('manufacturer_url','')).startswith('https://') else f"https://www.stockfinder.workers.dev/p/{p.get('slug',p['id'])}"
out=[]; skipped=collections.Counter()
def add(p,key,value,why):
    out.append(dict(product_id=p['id'],category_id=p['category_id'],key=key,value=value,source='manual',date=DATE,source_url=url(p),note='INFERIDO: '+why))
# --- peers por chip (GPU): valor único compartido por todos los productos con el mismo chip
peer=collections.defaultdict(lambda: collections.defaultdict(set))
for p in P.values():
    if p['category_id']!='gpu': continue
    chip=(p['specs'].get('Chip gráfico') or '').replace('NVIDIA ','').replace('AMD ','').replace('Intel ','').strip().lower()
    if not chip: continue
    for k in ('Interfaz','Consumo (TBP)','Consumo (TGP)','Fuente recomendada'):
        v=p['specs'].get(k)
        if v: peer[(chip,k)]['v'].add(re.sub(r'\s*\(.*\)','',v).strip())
for p in P.values():
    cat=p['category_id']; s=p['specs']; G=gaps(p); name=p['name']; low=(name+' '+p['id']).lower()
    if cat=='gpu':
        chip=(s.get('Chip gráfico') or '').replace('NVIDIA ','').replace('AMD ','').replace('Intel ','').strip().lower()
        for key,alts in (('Interfaz',()),('Consumo (TBP)',('Consumo (TGP)',)),('Fuente recomendada',())):
            if key not in G or not chip: continue
            vals=set(peer[(chip,key)]['v']); 
            for a in alts: vals|=peer[(chip,a)]['v']
            if len(vals)==1:
                v=vals.pop(); k2=key
                if key=='Consumo (TBP)' and chip.startswith(('rtx','geforce','gtx')): k2='Consumo (TGP)'
                if key=='Fuente recomendada' and not v.endswith('W'): continue
                add(p,k2,v,f'mismo chip ({chip}) que otros productos del catálogo con ese valor; es el valor de referencia del chip, no de esta variante.')
            else: skipped[('gpu',key,'sin consenso de chip' if vals else 'sin pares')]+=1
    if cat=='ram':
        t=s.get('Tipo de memoria','')
        if 'Formato' in G:
            if re.search(r'so-?dimm|portatil|portátil|laptop|\bsodimm',low) or re.fullmatch(r'.*kingston-(kvr|kcp|kf)\d+s.*',p['id']): v='SODIMM'
            elif re.search(r'ecc|rdimm|registered|lrdimm|cudimm|cu-dimm|csodimm|server|servidor|synology|apacer-el',low): v=None
            elif t in ('DDR4','DDR5') and (s.get('Módulos') or ''): v='UDIMM'
            else: v=None
            if v: add(p,'Formato',v,'memoria de sobremesa DIMM sin indicación de SO-DIMM/RDIMM/ECC/CUDIMM en nombre ni P/N; en consumo equivale a UDIMM.' if v=='UDIMM' else 'el nombre o el P/N indican SO-DIMM.')
            else: skipped[('ram','Formato','ECC/servidor/CUDIMM')]+=1
        if 'Latencia' in G and p.get('brand')=='Crucial':
            m=re.search(r'(\d{2})c(\d{2})u5',p['id'])
            if m: add(p,'Latencia',f'CL{int(m.group(2))}','el P/N de Crucial codifica la latencia (…C<CL>U5).')
            elif re.search(r'dfra32a|dfd832a|dfd8',p['id']) and s.get('Velocidad','').startswith('3200'): add(p,'Latencia','CL22','Crucial DDR4-3200 estándar JEDEC (CL22).')
    if cat=='case' and 'Formato fuente' in G:
        comp=s.get('Formatos compatibles','')
        if re.search(r'slim|sff|tfx|cube|itx\s*only|desktop',low) or p.get('brand')=='Coolbox': skipped[('case','Formato fuente','slim/TFX/marca')]+=1
        elif re.search(r'ATX|E-ATX|CEB',comp) and 'Mini-ITX'!=comp.strip(): add(p,'Formato fuente','ATX','torre que admite placas ATX/Micro-ATX; el formato de fuente estándar de estas cajas es ATX.')
        else: skipped[('case','Formato fuente','solo ITX o sin dato')]+=1
    if cat=='psu':
        if 'Estándar ATX' in G and s.get('Formato')=='ATX' and not re.search(r'16 pines|12v-2x6|12vhpwr|pcie 5',(s.get('Conector GPU','')+' '+name+' '+p['id']).lower()):
            add(p,'Estándar ATX','2.x','fuente ATX sin conector 12V-2x6/12VHPWR; las ATX 3.x lo exigen, así que es de la generación 2.x.')
        if 'Modular' in G:
            ml=re.search(r'full[- ]?modular|totalmente modular',low); sm=re.search(r'semi[- ]?modular',low)
            if ml: add(p,'Modular','Totalmente modular','el nombre del producto indica «full modular».')
            elif sm: add(p,'Modular','Semimodular','el nombre del producto indica «semi modular».')
            else: skipped[('psu','Modular','sin pista en el nombre')]+=1

# --- hermanos: misma marca y serie (nombre sin capacidad/potencia/color) con el mismo valor en TODOS los que lo tienen
def linekey(p):
    n=p['name'].lower(); n=re.sub(r'\(.*?\)','',n)
    n=re.sub(r'\d+([.,]\d+)?\s*(gb|tb|w|mm|mhz|mt/s|g)','',n)
    n=re.sub(r'cl\d+|(negr[oa]|blanc[oa]|black|white|rgb|argb|bulk|oc|edition)','',n); n=re.sub(r'[^a-z0-9áéíóúñ]+',' ',n).strip()
    sp=p['specs']; c=p['category_id']
    extra=sp.get('Chip gráfico') if c=='gpu' else (sp.get('Tipo de memoria'),sp.get('Velocidad')) if c=='ram' else sp.get('Potencia') if (c=='psu') else None
    return (c,p.get('brand'),n,str(extra))
SIB={'gpu':['Longitud','Interfaz','Consumo (TBP)','Fuente recomendada'],'ram':['Perfil','Latencia','Formato'],'psu':['Modular','Estándar ATX','Eficiencia 80 PLUS','Conector GPU'],
     'case':['Panel lateral','Longitud GPU máxima','Altura disipador máxima','Formato fuente'],'cooler':['Sockets compatibles','Ventiladores','Altura','Radiador'],'cpu':['Disipador incluido'],'storage':['Disipador integrado','Lectura secuencial','Formato']}
STOP={'geforce','radeon','nvidia','amd','intel','rtx','gddr','gaming','edition','black','white','negro','blanco','negra','blanca','rgb','argb','bulk','gen','ddr','dimm','udimm','kit','cl','oc','xmp','expo','pro'}
def linekey(p):
    toks=[t for t in re.findall(r'[a-záéíóúñ]{3,}',re.sub(r'\(.*?\)','',p['name'].lower())) if t not in STOP and t!=(p.get('brand') or '').lower()]
    sp=p['specs']; c=p['category_id']
    extra=sp.get('Chip gráfico') if c=='gpu' else (sp.get('Tipo de memoria'),sp.get('Velocidad')) if c=='ram' else sp.get('Potencia') if (c=='psu') else None
    return (c,p.get('brand'),' '.join(toks[:3]),str(extra))
groups=collections.defaultdict(list)
for p in P.values(): groups[linekey(p)].append(p)
have={(r['product_id'],r['key']) for r in out}
for p in P.values():
    sibs=[q for q in groups[linekey(p)] if q['id']!=p['id']]
    if not sibs: continue
    for key in SIB.get(p['category_id'],[]):
        if key not in gaps(p) or (p['id'],key) in have: continue
        alts=[x['alternates'] for x in p['gaps'] if x['key']==key][0]
        vals={}
        for q in sibs:
            for k in (key,*alts):
                v=q['specs'].get(k)
                if isinstance(v,str) and v.strip(): vals[q['id']]=(k,v)
        if not vals: continue
        distinct={v for _,v in vals.values()}
        if len(distinct)==1:
            k0=next(iter(vals.values()))[0]
            if key=='Longitud' and False: pass
            add(p,k0,distinct.pop(),f'mismo valor en {len(vals)} producto(s) hermano(s) de la misma serie ({", ".join(list(vals)[:3])}); no es dato de ficha de este modelo.'); have.add((p['id'],key))

# --- PSU de gama básica: <=750 W sin certificación 80 PLUS superior a Bronze -> cables fijos (política de precio de estas gamas)
have={(r['product_id'],r['key']) for r in out}
for p in P.values():
    if p['category_id']!='psu' or 'Modular' not in gaps(p) or (p['id'],'Modular') in have: continue
    sp=p['specs']; w=re.search(r'\d+',sp.get('Potencia','') or ''); eff=(sp.get('Eficiencia 80 PLUS') or '')+' '+(sp.get('Cybenetics') or '')
    if w and int(w.group())<=750 and not re.search(r'gold|platinum|titanium|silver',eff.lower()) and not re.search(r'modular',p['name'].lower()):
        add(p,'Modular','Cables fijos','gama básica (<=750 W, sin certificación superior a Bronze): estas series se venden con cableado fijo; no es dato de ficha.')
# validar
ok=[];bad=collections.Counter()
for r in out:
    e=so.validate_record(r)
    if e: bad[(r['key'],e[0][:90])]+=1
    else: ok.append(r)
c=collections.Counter((r['category_id'],r['key']) for r in ok)
print('válidos',len(ok),dict(c)); print('inválidos',dict(bad)); print('omitidos',dict(skipped))
seen=set();
for r in ok:
    kk=(r['category_id'],r['key'])
    if kk in seen: continue
    seen.add(kk); print('  ej',r['product_id'],r['key'],'=',r['value'])
if '--write' in sys.argv:
    by=collections.defaultdict(list)
    for r in ok: by[r['category_id']].append(r)
    for cat,rs in by.items(): (R/f'data/overrides/inferred-{DATE}-{cat}.json').write_text(json.dumps(rs,ensure_ascii=False,indent=2))
    print('escrito')
