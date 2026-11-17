"""Run on the host: repair plan-900 using the installed public IPC client."""
from pathlib import Path
import json
import msgspec
from aconsole.contracts.app import PlanIdParams, PlanSaveParams, PlanView
from aconsole.contracts.common import PlanId
from aconsole.contracts.ipc import ResponseOk
from aconsole.devcli.client import request_frame

ROOT = Path(__file__).resolve().parents[2]
SOCKET = '/run/user/1000/aconsole/core.sock'
OUT = ROOT / 'artifacts/idx-1/checks'

def call(method, params):
    response = request_frame(SOCKET, method, params, timeout=30)
    if not isinstance(response, ResponseOk):
        raise RuntimeError(msgspec.json.encode(response).decode())
    return msgspec.json.decode(response.result, type=PlanView)

before = call('plan/get', PlanIdParams(plan_id=PlanId('plan-900')))
(OUT/'live-before-repair.json').write_bytes(msgspec.json.encode(before))
profiles = json.loads((ROOT/'artifacts/plan-900-diagnosis/repair-gate-profiles.json').read_text())
after = call('plan/save', PlanSaveParams(
    project_id=before.project_id, plan_id=before.id, title=before.title, goal=before.goal,
    repair_gate_profiles={key: msgspec.Raw(msgspec.json.encode(profile)) for key,profile in profiles.items()},
))
assert [(n.id,n.key,n.status) for n in after.nodes] == [(n.id,n.key,n.status) for n in before.nodes]
assert (after.title,after.goal,after.phase) == (before.title,before.goal,'frozen')
(OUT/'live-after-repair.json').write_bytes(msgspec.json.encode(after))
print(f'Repaired {after.id}; {len(after.nodes)} nodes preserved; phase={after.phase}')
