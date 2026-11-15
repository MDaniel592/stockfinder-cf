import { useEffect, useState } from 'react';
import { DropCards, type Drop } from './Home';
import { absoluteDate, relativeAge } from './format';
const CATEGORIES = [['','Todos los componentes'],['cpu','Procesadores'],['gpu','Tarjetas gráficas'],['motherboard','Placas base'],['ram','Memoria RAM'],['storage','Almacenamiento'],['psu','Fuentes'],['case','Cajas'],['cooler','Refrigeración']];
export function DealsPage() {
  const [category,setCategory] = useState('');
  const [data,setData] = useState<{drops:Drop[];calculated_at?:string;stale?:boolean}|null>(null);
  const [error,setError] = useState(''),[retry,setRetry]=useState(0),[loading,setLoading]=useState(true);
  useEffect(()=>{
    const controller=new AbortController(); setLoading(true);setError('');setData(null);
    fetch(`/api/v1/catalog/deals${category ? `?category=${category}` : ''}`,{signal:controller.signal,headers:retry ? {'Cache-Control':'no-cache'} : undefined})
      .then(async r=>{if(!r.ok)throw new Error();const body=await r.json() as {data:NonNullable<typeof data>};if(!controller.signal.aborted)setData(body.data);})
      .catch(()=>{if(!controller.signal.aborted)setError('No se pudieron consultar las ofertas. Inténtalo de nuevo.');})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[retry,category]);
  return <section className="home deals-page"><header className="deals-header"><h1>{category ? 'Top 10 de la categoría' : 'Las 20 mayores bajadas'}</h1><p>Las mayores bajadas de las últimas 24 horas, ordenadas por porcentaje.</p></header>
    <div className="deals-toolbar"><label>Componente<select aria-label="Tipo de componente" value={category} onChange={e=>setCategory(e.target.value)}>{CATEGORIES.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label><div className="deals-update">
      {data?.calculated_at && <span title={absoluteDate(data.calculated_at)}>Actualizado {relativeAge(data.calculated_at)}</span>}
      <button type="button" className="text-button" disabled={loading} onClick={()=>setRetry(v=>v+1)}>{loading?'Consultando…':'Actualizar'}</button>
    </div></div>
    {error && <p role="alert">{error}</p>}
    {data?.stale && <p role="alert">La réplica no se ha actualizado recientemente. No mostramos sus precios como ofertas actuales hasta recuperar la sincronización.</p>}
    {!data && loading && <p role="status">Cargando el ranking de ofertas…</p>}
    {data && !data.stale && (data.drops.length ? <DropCards drops={data.drops}/> : <p>No hay bajadas verificadas que cumplan estos criterios en las últimas 24 horas.</p>)}
  </section>;
}
