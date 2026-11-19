"""Read GAT-2 evidence without changing the application database."""
import json
import pathlib
import sqlite3

base = pathlib.Path(__file__).parent
connection = sqlite3.connect('file:/home/sas/.local/share/aconsole/aconsole.db?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
for table, order in (('gate_receipts', 'created_at'), ('node_attempts', 'started_at')):
    rows = [dict(row) for row in connection.execute(
        f'SELECT * FROM {table} WHERE node_id = ? ORDER BY {order}', ('node-917',))]
    (base / f'{table}.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    for row in rows:
        if table == 'gate_receipts':
            print(row['tier'], row['passed'], row['receipt_hash'], row['compact_verdict'], row['state_hash'])
            if row['log_ref'] and pathlib.Path(row['log_ref']).exists():
                (base / (row['receipt_hash'] + '.log')).write_bytes(pathlib.Path(row['log_ref']).read_bytes())
        else:
            print('attempt', row['attempt_no'], row['status'], row['compact_verdict'])
connection.close()
