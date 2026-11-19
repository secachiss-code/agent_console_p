"""Replay the saved public scope repair with its original expected hash (normally stale after success)."""
import pathlib
import runpy
import msgspec
from aconsole.devcli.client import request_frame

base = pathlib.Path(__file__).parent
schema = runpy.run_path('artifacts/vlt1-fix-2026-11-18/apply_repair.py')['ScopeRepair']
params = msgspec.json.decode((base / 'repair-plan.json').read_bytes(), type=schema)
response = request_frame('/run/user/1000/aconsole/core.sock', 'plan/save', params, timeout=60)
print(msgspec.json.encode(response).decode())
