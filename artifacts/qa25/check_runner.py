"""Finite runner failure controls; no GTK, source assertions or PNG changes."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('native_ui_tests', root/'tools/native_ui_tests.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
results = {}
with tempfile.TemporaryDirectory(prefix='qa25-controls-') as directory:
    temp = Path(directory)
    # The real pytest process fails collection with exit code 4.
    env = dict(os.environ, PYTHONPATH=str(root/'src'), GDK_BACKEND='x11')
    error = temp/'error'
    error.mkdir()
    results['pytest_usage_error'] = module.run_tests(env, ['no-such-qa25-test.py'], error, 20)
    assert results['pytest_usage_error'] == 4
    slow = temp/'test_slow.py'
    slow.write_text('import time\ndef test_slow():\n    time.sleep(30)\n')
    timeout = temp/'timeout'
    timeout.mkdir()
    results['timeout'] = module.run_tests(env, [str(slow)], timeout, 0.5)
    assert results['timeout'] == 124
    for label in ['error', 'timeout']:
        (root/'artifacts/qa25'/f'control-{label}.log').write_bytes((temp/label/'pytest.log').read_bytes())
    broken = temp/'broken'
    broken.mkdir()
    try:
        with module.private_display('/bin/false', broken):
            raise AssertionError('failed server yielded a display')
    except RuntimeError as exc:
        results['server_failure'] = str(exc)
    # Start a real process in its own group and confirm it is reaped.
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    module.stop_group(child)
    assert child.poll() is not None
    results['reaped_process'] = child.returncode
    # A child that ignores TERM remains in the owned process group. The runner
    # must still kill it when the parent has already exited successfully.
    child_pid = temp/'child-pid'
    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
    parent_code = ("import subprocess,sys,time; from pathlib import Path; "
                   "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); "
                   "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(.2)")
    parent = subprocess.Popen([sys.executable, '-c', parent_code, str(child_pid), code],
                              start_new_session=True)
    assert parent.wait(timeout=5) == 0
    module.stop_group(parent)
    import time
    descendant = int(child_pid.read_text())
    for _ in range(100):
        status = Path(f'/proc/{descendant}/stat')
        if not status.exists() or status.read_text().split()[2] == 'Z':
            break
        time.sleep(.01)
    else:
        raise AssertionError('owned descendant survived cleanup')
    results['terminated_descendant_after_parent_exit'] = True
    existing = temp/'existing'
    existing.mkdir()
    run = subprocess.run([sys.executable, str(root/'tools/native_ui_tests.py'), '--mode', 'geometry',
                          '--xvfb', '/bin/false', '--output', str(existing)], capture_output=True)
    assert run.returncode != 0 and b'FileExistsError' in run.stderr
    results['refuses_overwrite'] = True
(root/'artifacts/qa25/runner-controls.json').write_text(json.dumps(results, indent=2)+'\n')
print(results)
