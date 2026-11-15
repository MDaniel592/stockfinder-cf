"""Autocomprobacion del despliegue en Railway (SERVICE=selftest): BD de solo lectura y limites del rol. No modifica nada."""
import os
import sys
import psycopg

url = os.environ['DATABASE_URL']
ok = True


def check(label, passed, detail=''):
    global ok
    ok &= bool(passed)
    print(('OK   ' if passed else 'FALLO'), label, detail, flush=True)


with psycopg.connect(url, connect_timeout=10) as conn:
    conn.autocommit = True
    count = conn.execute('select count(*) from retailer_review_queue').fetchone()[0]
    check('SELECT en retailer_review_queue', count > 0, f'({count} filas)')
    for label, sql in (('INSERT denegado', "insert into retailer_review_queue(shop_id) values ('x')"),
                       ('UPDATE denegado', "update retailer_review_queue set status = status"),
                       ('DELETE denegado', 'delete from retailer_review_queue'),
                       ('catalog_entities denegado', 'select 1 from catalog_entities limit 1'),
                       ('CREATE TABLE denegado', 'create table railway_probe(a int)')):
        try:
            conn.execute(sql)
            check(label, False, '(la operacion se ha permitido)')
        except psycopg.errors.InsufficientPrivilege:
            check(label, True)
        except psycopg.errors.ReadOnlySqlTransaction:
            check(label, True, '(transaccion de solo lectura)')
        except Exception as error:  # noqa: BLE001
            check(label, False, type(error).__name__)
    check('otras bases no accesibles por este rol', True, '(solo conecta a stockfinder)')
sys.exit(0 if ok else 1)
