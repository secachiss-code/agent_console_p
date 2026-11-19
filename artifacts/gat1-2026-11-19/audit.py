import json
import pathlib
import sqlite3

base = pathlib.Path(__file__).parent
connection = sqlite3.connect('file:/home/sas/.local/share/aconsole/aconsole.db?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
for table in ('gate_receipts', 'node_attempts'):
    rows = [dict(row) for row in connection.execute(f'SELECT * FROM {table} WHERE node_id = ? ORDER BY created_at' if table == 'gate_receipts' else f'SELECT * FROM {table} WHERE node_id = ? ORDER BY started_at', ('node-916',))]
    (base / f'{table}.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    if table == 'gate_receipts':
        for row in rows:
            print(row['tier'], row['passed'], row['receipt_hash'], row['compact_verdict'], row['log_ref'])
            if row['log_ref'] and pathlib.Path(row['log_ref']).exists():
                (base / (row['receipt_hash'] + '.log')).write_bytes(pathlib.Path(row['log_ref']).read_bytes())
connection.close()
