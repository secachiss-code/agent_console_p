"""Record explicit user acceptance through the public native CLI handshake."""
import pathlib
import sys
import msgspec
from aconsole.devcli.client import request_frame
from aconsole.contracts.app import SetNodeStatusParams
from aconsole.contracts.common import NodeId, NodeStatus
from aconsole.contracts.ipc import ResponseOk

node = sys.argv[1]
if node not in ('node-905', 'node-910'):
    raise SystemExit('Only explicitly approved IDX-4 and LOD-3 may be accepted')
note = ('Пользователь явно подтвердил приёмку IDX-4 и LOD-3 сообщением «Прими» '
        'после отчёта о зелёных Micro/Blast и ожидающей human-приёмке. '
        'Принят ' + ('IDX-4: опубликованный DLL/dotnet exec, implements, без повторной сборки; offline AOT описан как необязательный спайк.'
                    if node == 'node-905' else 'LOD-3: два тела сохраняют bytes/SHA префикса, смена сигнатуры меняет их, порядок файлов стабилен.'))
params = SetNodeStatusParams(node_id=NodeId(node), status=NodeStatus.DONE, note=note)
response = request_frame('/run/user/1000/aconsole/core.sock', 'plan/setNodeStatus', params, timeout=60)
raw = msgspec.json.encode(response)
(pathlib.Path(__file__).parent / (node + '-response.json')).write_bytes(raw)
print(raw.decode())
raise SystemExit(0 if isinstance(response, ResponseOk) else 1)
