import { Search } from 'lucide-react';
import { Link } from './Link';

export function AboutPage() {
  return <article className="info-page">
    <h1>Cómo funciona StockFinder</h1>
    <p className="info-lead">StockFinder es un comparador de componentes de PC: reúne especificaciones oficiales y precios de tiendas españolas para que puedas comparar piezas, ver su historial de precio y montar una configuración básica.</p>
    <h2>De dónde salen los datos</h2>
    <ul>
      <li><strong>Especificaciones:</strong> se toman de las fichas oficiales de cada fabricante, y cada producto enlaza a la suya.</li>
      <li><strong>Identificación:</strong> una oferta solo se asocia a un producto cuando su referencia (MPN) o código de barras (EAN) coincide exactamente. No se emparejan productos por parecido de nombre.</li>
      <li><strong>El símbolo «≈»:</strong> aparece junto a una referencia cuando la ficha del fabricante no publica el código y se usa el que indica la tienda. El modelo está verificado en la ficha oficial, pero el código está pendiente de confirmar.</li>
      <li><strong>Referencias:</strong> en cada producto puedes ver la referencia del fabricante y, si existen, los códigos comerciales con los que lo vende cada tienda.</li>
    </ul>
    <h2>Precio y stock</h2>
    <ul>
      <li>Cada oferta indica cuándo se comprobó por última vez su precio y su disponibilidad. Si la comprobación es antigua, lo marcamos como «Desactualizado» o «Sin verificar».</li>
      <li>El precio «Desde» es el más bajo entre las ofertas con stock. El histórico muestra cómo ha cambiado ese precio mínimo.</li>
      <li>El precio final, los gastos de envío, los impuestos y las condiciones para Canarias, Ceuta y Melilla los decide cada tienda. Confírmalos siempre en su web antes de comprar.</li>
      <li>StockFinder no vende productos: los botones «Ver en tienda» te llevan a la página de la tienda.</li>
    </ul>
    <h2>Configurador de PC</h2>
    <p>El <Link href="/configurador">configurador</Link> hace comprobaciones básicas de compatibilidad (por ejemplo, socket, tipo de memoria o formato de la caja). Es una ayuda orientativa: no certifica la compatibilidad de BIOS ni las listas de memorias compatibles (QVL) del fabricante de la placa. Consulta siempre las especificaciones del fabricante antes de comprar.</p>
    <h2>¿Has visto un error?</h2>
    <p>En cada producto y en cada oferta hay un botón para reportar errores. Revisamos los avisos para corregir datos incorrectos.</p>
    <p className="info-links"><Link href="/privacidad">Política de privacidad</Link> · <Link href="/">Volver al catálogo</Link></p>
  </article>;
}

export function PrivacyPage() {
  return <article className="info-page">
    <h1>Privacidad</h1>
    <p className="info-lead">StockFinder está pensado para usarse sin crear cuenta y con los mínimos datos posibles.</p>
    <h2>Lo que no hacemos</h2>
    <ul>
      <li>No hay cuentas de usuario ni inicio de sesión.</li>
      <li>No usamos cookies, ni de seguimiento ni de publicidad.</li>
    </ul>
    <h2>Lo que se guarda en tu navegador</h2>
    <p>Usamos el almacenamiento local (<code>localStorage</code>) únicamente para recordar en tu dispositivo tus listas del configurador, sus capturas de precios, tus favoritos y objetivos de precio, y tus preferencias de apariencia. Los favoritos se comprueban al abrir la web consultando los productos seleccionados en nuestra API. Las listas compartidas por enlace se guardan en el servidor con su nombre y componentes, sin los precios. El resto de estas preferencias permanece en tu dispositivo y puedes borrarlos desde los ajustes de tu navegador.</p>
    <h2>Formulario de reporte de errores</h2>
    <p>Cuando envías un reporte guardamos el texto que escribes, el producto u oferta al que se refiere y un hash (huella no reversible) de tu dirección IP, que usamos solo para limitar los envíos a 5 por hora y evitar abusos. No guardamos tu dirección IP en claro.</p>
    <h2>Infraestructura</h2>
    <p>El sitio funciona sobre Cloudflare, que procesa el tráfico para servir las páginas y proteger el servicio, y puede registrar datos técnicos de conexión según sus propias políticas.</p>
    <h2>Enlaces a tiendas</h2>
    <p>Al pulsar «Ver en tienda» o «Buscar en…» sales de StockFinder hacia una web externa, que aplica su propia política de privacidad y de cookies.</p>
    <p className="info-links"><Link href="/acerca">Cómo funciona StockFinder</Link> · <Link href="/">Volver al catálogo</Link></p>
  </article>;
}

export function NotFoundPage({ title = 'Página no encontrada', text = 'La dirección no existe o ha cambiado.' }: { title?: string; text?: string }) {
  return <div className="empty not-found">
    <Search size={36} aria-hidden="true"/><h1>{title}</h1><p>{text}</p>
    <Link className="primary link-button" href="/">Ir al catálogo</Link>
    <p className="not-found-links"><Link href="/c/gpu">Tarjetas gráficas</Link><Link href="/c/cpu">Procesadores</Link><Link href="/configurador">Configurador de PC</Link><Link href="/acerca">Cómo funciona</Link></p>
  </div>;
}
