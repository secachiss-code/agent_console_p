import pathlib
import runpy
import sys
import msgspec
from aconsole.devcli.client import request_frame
from aconsole.contracts.app import SetNodeStatusParams
from aconsole.contracts.common import NodeId, NodeStatus
from aconsole.contracts.ipc import ResponseOk
base = pathlib.Path(__file__).parent
if sys.argv[1] == 'repair':
    namespace = runpy.run_path('artifacts/vlt1-fix-2026-11-18/apply_repair.py')
    params = msgspec.json.decode((base / 'repair-plan.json').read_bytes(), type=namespace['ScopeRepair'])
    method = 'plan/save'
else:
    params = SetNodeStatusParams(node_id=NodeId('node-913'), status=NodeStatus.PENDING,
        note='Continue authorized LOD-2 implementation after scoped regression repair; rerun fresh pipeline checks.')
    method = 'plan/setNodeStatus'
response = request_frame('/run/user/1000/aconsole/core.sock', method, params, timeout=60)
raw = msgspec.json.encode(response)
(base / (sys.argv[1] + '-response.json')).write_bytes(raw)
print(raw.decode())
sys.exit(0 if isinstance(response, ResponseOk) else 1)
