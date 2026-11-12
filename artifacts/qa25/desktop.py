"""Read-only desktop records plus unchanged native lifecycle/action tests."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root/'src'))
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.views.app import AConsoleApp

app = AConsoleApp(CoreClient('/unused.sock'))
identity = app.get_application_id()
assert identity == 'io.github.aconsole.AConsole'

def rows():
    db = Path.home()/'.local/share/io.elementary.gala/windowstate.db'
    with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as connection:
        return connection.execute('SELECT * FROM apps WHERE app_id = ? ORDER BY window_index',
                                  (identity+'.desktop',)).fetchall()

output = root/'artifacts/qa25/desktop-state.json'
with output.open('x') as stream:
    before = rows()
    result = subprocess.run([sys.executable, str(root/'tools/native_ui_tests.py'), '--mode', 'desktop',
                             '--output', str(root/'artifacts/qa25/native-desktop')], cwd=root, timeout=150)
    after = rows()
    json.dump(dict(application_id=identity, display=os.getenv('DISPLAY'), before=before, after=after,
                   returncode=result.returncode), stream, indent=2)
    stream.write('\n')
assert before == after
sys.exit(result.returncode)
