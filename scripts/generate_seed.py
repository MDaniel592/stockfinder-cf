"""Deterministic synthetic catalogue. Does not contact any retailer."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = [('gpu', 'Tarjetas gráficas'), ('cpu', 'Procesadores'), ('motherboard', 'Placas base'),
              ('ram', 'Memoria RAM'), ('storage', 'Almacenamiento'), ('psu', 'Fuentes de alimentación'),
              ('case', 'Cajas'), ('cooler', 'Refrigeración')]
PARTS = [
 ('gpu', 'NVIDIA', 'GeForce RTX 5070', 59900, {'Memoria': '12 GB GDDR7', 'Consumo': '250 W', 'Interfaz': 'PCIe 5.0'}),
 ('gpu', 'AMD', 'Radeon RX 9070 XT', 67900, {'Memoria': '16 GB GDDR6', 'Consumo': '304 W', 'Interfaz': 'PCIe 5.0'}),
 ('gpu', 'NVIDIA', 'GeForce RTX 5080', 119900, {'Memoria': '16 GB GDDR7', 'Consumo': '360 W', 'Interfaz': 'PCIe 5.0'}),
 ('cpu', 'AMD', 'Ryzen 7 9800X3D', 46900, {'Socket': 'AM5', 'Núcleos': '8', 'Consumo': '120 W'}),
 ('cpu', 'Intel', 'Core Ultra 7 265K', 32900, {'Socket': 'LGA1851', 'Núcleos': '20', 'Consumo': '125 W'}),
 ('cpu', 'AMD', 'Ryzen 5 9600X', 22900, {'Socket': 'AM5', 'Núcleos': '6', 'Consumo': '65 W'}),
 ('motherboard', 'ASUS', 'TUF Gaming B850-Plus WiFi', 21900, {'Socket': 'AM5', 'Memoria': 'DDR5', 'Formato': 'ATX'}),
 ('ram', 'Corsair', 'Vengeance 32 GB DDR5', 10900, {'Capacidad': '32 GB', 'Velocidad': '6000 MT/s', 'Latencia': 'CL30'}),
 ('storage', 'Samsung', '990 Pro 2 TB', 15900, {'Capacidad': '2 TB', 'Interfaz': 'PCIe 4.0', 'Formato': 'M.2 2280'}),
 ('psu', 'Corsair', 'RM850e 850 W', 11900, {'Potencia': '850 W', 'Certificación': '80+ Gold', 'Modular': 'Sí'}),
 ('case', 'Fractal', 'North Charcoal', 14900, {'Formato': 'ATX', 'Color': 'Negro', 'Panel': 'Malla'}),
 ('cooler', 'Arctic', 'Liquid Freezer III 360', 8900, {'Radiador': '360 mm', 'Tipo': 'Líquida', 'Socket': 'AM5 / LGA1851'})
]

def events(count=120, changed_at=None):
    now = changed_at or datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    output = []
    def add(entity, entity_id, data):
        output.append(dict(event_id=len(output)+1, entity=entity, entity_id=entity_id, version=1,
                           operation='upsert', changed_at=now, data=data))
    for slug, name in CATEGORIES:
        add('category', slug, dict(slug=slug, name=name))
    for index in range(2):
        add('shop', f'shop-{index}', dict(name=['Tienda Atlas · Demo', 'Tienda Nova · Demo'][index],
                                       url_public=f'https://shop-{index}.example.com'))
    for index in range(count):
        category, brand, name, price, specs = PARTS[index % len(PARTS)]
        variant = index // len(PARTS)
        product_id = f'part-{index:04d}'
        full_name = f'{brand} {name}' + (f' · Edición {variant + 1}' if variant else '')
        add('product', product_id, dict(slug=product_id, name=full_name, category_id=category, brand=brand, specs=specs))
        for shop in range(2):
            add('offer', f'offer-{index:04d}-{shop}', dict(product_id=product_id, shop_id=f'shop-{shop}',
                price_cents=price + variant*700 + shop*2100, in_stock=(index+shop) % 7 != 0,
                last_checked_at=now, url_public=f'https://shop-{shop}.example.com/products/{product_id}'))
    return output

def sql(events_list):
    def quote(value):
        if value is None: return 'NULL'
        if isinstance(value, bool): return str(int(value))
        if isinstance(value, (float, int)): return str(value)
        if isinstance(value, dict): value = json.dumps(value, ensure_ascii=False)
        return "'" + value.replace("'", "''") + "'"
    tables = dict(category='catalog_categories', shop='catalog_shops', product='catalog_products', offer='catalog_offers')
    statements = []
    for e in events_list:
        data = dict(id=e['entity_id'], version=e['version'], changed_at=e['changed_at'], **e['data'])
        columns = list(data)
        updates = ','.join(f'{c}=excluded.{c}' for c in columns if c != 'id')
        table = tables[e['entity']]
        statements.append(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join(map(quote, data.values()))}) ON CONFLICT(id) DO UPDATE SET {updates} WHERE excluded.version>{table}.version;")
    now = events_list[-1]['changed_at']
    statements.append(f'UPDATE sync_status SET last_event_id={len(events_list)}, last_sync_at={quote(now)}, source_changed_at={quote(now)} WHERE id=1;')
    return '\n'.join(statements)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.count <= 1000: parser.error('count must be between 1 and 1000')
    out = Path('artifacts'); out.mkdir(exist_ok=True)
    data = events(args.count)
    (out/'seed.sql').write_text(sql(data))
    (out/'seed-events.json').write_text(json.dumps(data, ensure_ascii=False))
    print(f'{args.count} synthetic products, {len(data)} events generated')
