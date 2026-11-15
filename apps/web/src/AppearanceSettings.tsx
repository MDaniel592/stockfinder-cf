import { useLayoutEffect, useRef, useState } from 'react';
import { Check, Monitor, Palette, RotateCcw, Settings2, X } from 'lucide-react';

type Theme = 'light' | 'dark' | 'blue' | 'system';
type Preferences = { theme: Theme; compact: boolean; reducedMotion: boolean; showFavorites: boolean };
const storageKey = 'stockfinder.appearance.v1';
const defaults: Preferences = { theme: 'light', compact: false, reducedMotion: false, showFavorites:true };
const themes: { value: Theme; name: string; description: string }[] = [
  { value: 'light', name: 'Porcelana', description: 'Claro · acento rosa (predeterminado)' },
  { value: 'dark', name: 'Obsidiana', description: 'Negro neutro · acento rosa' },
  { value: 'blue', name: 'Medianoche', description: 'Azul pizarra · acento azul' },
  { value: 'system', name: 'Sistema', description: 'Claro u oscuro según tu dispositivo' }
];

function restore(): Preferences {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
    if (!saved || typeof saved !== 'object') return defaults;
    const candidate = saved as Partial<Preferences>;
    return {
      theme: themes.some(theme => theme.value === candidate.theme) ? candidate.theme! : defaults.theme,
      compact: typeof candidate.compact === 'boolean' ? candidate.compact : defaults.compact,
      reducedMotion: typeof candidate.reducedMotion === 'boolean' ? candidate.reducedMotion : defaults.reducedMotion,
      showFavorites: typeof candidate.showFavorites === 'boolean' ? candidate.showFavorites : defaults.showFavorites
    };
  } catch { return defaults; }
}

export function AppearanceSettings() {
  const [preferences, setPreferences] = useState<Preferences>(restore);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const media = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;
    const apply = () => { root.dataset.theme = preferences.theme === 'system' ? (media?.matches ? 'dark' : 'light') : preferences.theme; };
    apply();
    root.dataset.density = preferences.compact ? 'compact' : 'comfortable';
    root.dataset.motion = preferences.reducedMotion ? 'reduced' : 'system';
    try { localStorage.setItem(storageKey, JSON.stringify(preferences)); } catch { /* Keep the current session usable when storage is unavailable. */ }
    window.dispatchEvent(new Event('sf-appearance'));
    if (preferences.theme !== 'system' || !media) return;
    media.addEventListener('change', apply); return () => media.removeEventListener('change', apply);
  }, [preferences]);
  const close = () => dialog.current?.close();
  return <>
    <button ref={trigger} className="appearance-trigger" aria-label="Ajustes de apariencia" title="Ajustes de apariencia"
      aria-haspopup="dialog" onClick={() => dialog.current?.showModal()}>
      <Settings2 size={17} aria-hidden="true"/><span>Apariencia</span>
    </button>
    <dialog ref={dialog} className="appearance-dialog" aria-labelledby="appearance-title" aria-describedby="appearance-description"
      onClose={() => trigger.current?.focus()} onClick={event => { if (event.target === dialog.current) close(); }}>
      <div className="appearance-panel">
        <div className="appearance-heading"><div className="appearance-heading-icon"><Palette size={21} aria-hidden="true"/></div>
          <div><h2 id="appearance-title">Apariencia</h2><p id="appearance-description">Tema, densidad y movimiento de la interfaz.</p></div>
          <button className="appearance-close" aria-label="Cerrar ajustes de apariencia" onClick={close}><X size={20} aria-hidden="true"/></button>
        </div>
        <fieldset className="appearance-themes"><legend>Tema de la interfaz</legend>
          {themes.map(theme => <label key={theme.value} className={`appearance-theme ${preferences.theme === theme.value ? 'is-selected' : ''}`}>
            <input type="radio" name="appearance-theme" value={theme.value} checked={preferences.theme === theme.value}
              onChange={() => setPreferences(current => ({ ...current, theme: theme.value }))}/>
            <span className={`appearance-swatch swatch-${theme.value}`} aria-hidden="true"><i/><i/><i/></span>
            <span className="appearance-theme-name"><strong>{theme.name}</strong><small>{theme.description}</small></span>
            <span className="appearance-radio" aria-hidden="true">{preferences.theme === theme.value && <Check size={13}/>}</span>
          </label>)}
        </fieldset>
        <div className="appearance-options">
          <label className="appearance-option"><span><strong>Mostrar favoritos en cabecera</strong><small>Ocultarlo no borra tus favoritos ni avisos.</small></span>
            <input type="checkbox" role="switch" checked={preferences.showFavorites} onChange={event => setPreferences(current => ({ ...current, showFavorites:event.target.checked }))}/>
          </label>
          <label className="appearance-option"><span><strong>Modo compacto</strong><small>Menos relleno y espacios en toda la web.</small></span>
            <input type="checkbox" role="switch" checked={preferences.compact} onChange={event => setPreferences(current => ({ ...current, compact: event.target.checked }))}/>
          </label>
          <label className="appearance-option"><span><strong>Reducir movimiento</strong><small>Desactiva animaciones y transiciones decorativas.</small></span>
            <input type="checkbox" role="switch" checked={preferences.reducedMotion} onChange={event => setPreferences(current => ({ ...current, reducedMotion: event.target.checked }))}/>
          </label>
        </div>
        <p className="appearance-footnote"><Monitor size={13} aria-hidden="true"/> Los cambios se aplican a este navegador.</p>
        <div className="appearance-actions"><button className="appearance-reset" onClick={() => setPreferences({ ...defaults })}><RotateCcw size={14} aria-hidden="true"/>Restablecer</button>
          <button className="primary" onClick={close}>Listo<Check size={14} aria-hidden="true"/></button></div>
      </div>
    </dialog>
  </>;
}
