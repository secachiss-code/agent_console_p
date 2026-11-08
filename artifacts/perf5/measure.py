"""Observe the unchanged scale test; optional profiling is a separate diagnostic run."""
import cProfile
import gc
import json
import os
from pathlib import Path
import time


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def pytest_runtest_call(item):
    if item.name != 'test_scrolling_a_long_history':
        return
    from tests.gui.front import test_git_scale_ui as test
    original = test._on_pane

    def on_pane(tmp_path, monkeypatch, repo, check):
        async def observed(pane, toasts, window):
            data = dict(renderer=window.get_renderer().__gtype__.name,
                python=os.sys.version, gtk_env={k:v for k,v in os.environ.items()
                    if k.startswith(('GSK_', 'GDK_', 'LIBGL_', 'LP_'))},
                cpu_count=os.cpu_count(), affinity=sorted(os.sched_getaffinity(0)),
                cpu_max=read('/sys/fs/cgroup/cpu.max'),
                cpu_stat_before=read('/sys/fs/cgroup/cpu.stat'),
                load_before=os.getloadavg(), proc_before=read('/proc/self/status'),
                frames=[], gc=[])
            started=time.perf_counter()
            cpu=time.process_time()
            thread=time.thread_time()
            last=[started,cpu,thread]
            def tick(widget, clock):
                now=[time.perf_counter(),time.process_time(),time.thread_time()]
                data['frames'].append(dict(at=now[0]-started,
                    wall=now[0]-last[0],cpu=now[1]-last[1],thread=now[2]-last[2],
                    rows=pane.store.get_n_items(),scroll=pane.scroller.get_vadjustment().get_value()))
                last[:]=now
                return True
            def collection(phase,info):
                data['gc'].append(dict(phase=phase,at=time.perf_counter()-started,**info))
            handle=pane.list_view.add_tick_callback(tick)
            gc.callbacks.append(collection)
            profile=cProfile.Profile() if os.environ.get('ACONSOLE_PERF5_PROFILE') else None
            try:
                if profile: profile.enable()
                await check(pane,toasts,window)
            finally:
                if profile:
                    profile.disable()
                    profile.dump_stats(os.environ['ACONSOLE_PERF5_PROFILE'])
                gc.callbacks.remove(collection)
                pane.list_view.remove_tick_callback(handle)
                data.update(cpu_stat_after=read('/sys/fs/cgroup/cpu.stat'),
                    load_after=os.getloadavg(),proc_after=read('/proc/self/status'),
                    rows=pane.store.get_n_items(),wall=time.perf_counter()-started,
                    cpu=time.process_time()-cpu,thread=time.thread_time()-thread)
                Path(os.environ['ACONSOLE_PERF5_LOG']).write_text(json.dumps(data)+'\n')
        return original(tmp_path,monkeypatch,repo,observed)
    monkeypatch = item._request.getfixturevalue('monkeypatch')
    monkeypatch.setattr(test,'_on_pane',on_pane)
