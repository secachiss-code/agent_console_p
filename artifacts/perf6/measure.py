"""Observe one existing benchmark without changing its assertions, GC policy or data."""
import gc
import json
import os
from pathlib import Path
import resource
import time

record = None
original = None
module = None


def collection(phase, info):
    if record is not None and record['active']:
        record['gc'].append(dict(phase=phase,generation=info['generation'],at=time.perf_counter_ns(),
            collected=info.get('collected',0),uncollectable=info.get('uncollectable',0)))


def pytest_runtest_setup(item):
    global record, original, module
    if item.name != 'test_rank_2000_under_16ms':
        return
    from aconsole.ui.viewmodels.logic import rank
    module = rank
    original = rank.rank_items
    record = dict(active=False,gc=[],wall_ns=0,thread_ns=0,process_ns=0,items=0,
                  before_gc=gc.get_count(),gc_threshold=gc.get_threshold())
    gc.callbacks.append(collection)

    def measured(items, query, **options):
        record['items'] = len(items)
        start_wall = time.perf_counter_ns()
        start_thread = time.thread_time_ns()
        start_process = time.process_time_ns()
        record['active'] = True
        try:
            return original(items, query, **options)
        finally:
            record['active'] = False
            record['wall_ns'] = time.perf_counter_ns() - start_wall
            record['thread_ns'] = time.thread_time_ns() - start_thread
            record['process_ns'] = time.process_time_ns() - start_process
    rank.rank_items = measured


def pytest_runtest_teardown(item):
    global record, original, module
    if item.name != 'test_rank_2000_under_16ms' or record is None:
        return
    module.rank_items = original
    gc.callbacks.remove(collection)
    record['after_gc'] = gc.get_count()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    record['max_rss_kb'] = usage.ru_maxrss
    record['pid'] = os.getpid()
    with Path(os.environ['ACONSOLE_PERF6_LOG']).open('a') as stream:
        stream.write(json.dumps(record)+'\n')
    record = None
