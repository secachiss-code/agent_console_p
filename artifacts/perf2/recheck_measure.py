"""Observe the original helper's result; do not change its work, timing or assertions."""
import functools,json,os,sys,time
from pathlib import Path

def context():
    data={'loadavg':os.getloadavg(),'monotonic':time.monotonic(),'pid':os.getpid()}
    for name in ['pressure/cpu','pressure/io','pressure/memory','self/cgroup']:
        try:data[name]=Path('/proc',name).read_text()
        except OSError:pass
    for name in ['cpu.max','cpu.stat','memory.max','memory.current']:
        try:data['cgroup/'+name]=Path('/sys/fs/cgroup',name).read_text()
        except OSError:pass
    return data

def pytest_runtest_setup(item):
    if item.name!='test_persistence_b_tick':return
    module=item.module
    original=module._tick_lags
    @functools.wraps(original)
    async def measured(path):
        before=context()
        lags,stored=await original(path)
        after=context()
        ordered=sorted(lags)
        row={'test':item.nodeid,'python':sys.version,'gil_enabled':sys._is_gil_enabled(),
             'before':before,'after':after,'n':len(ordered),'p50_s':ordered[len(ordered)//2],
             'p99_s':ordered[len(ordered)*99//100],'max_s':ordered[-1],'stored':stored,
             'limit_s':module._TICK_LIMIT_S,'rate':module._TICK_RATE,'span_s':module._TICK_SPAN_S}
        with Path(os.environ['ACONSOLE_PERF2_LOG']).open('a') as stream:stream.write(json.dumps(row)+'\n')
        return lags,stored
    module._tick_lags=measured
    item._perf2_original=original

def pytest_runtest_teardown(item):
    if hasattr(item,'_perf2_original'):item.module._tick_lags=item._perf2_original
