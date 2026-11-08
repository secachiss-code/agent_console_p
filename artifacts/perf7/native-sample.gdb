set pagination off
set confirm off
set print frame-arguments none
set print thread-events off
set style enabled off
set debuginfod enabled off
handle SIGINT stop print nopass
python
import gdb
import os
from pathlib import Path

marker=os.environ['ACONSOLE_NATIVE_STACK_MARKER']
stream=open(os.environ['ACONSOLE_NATIVE_STACK_LOG'],'w')
try:
    gdb.execute('starti',to_string=True)
    pid=gdb.selected_inferior().pid
    start=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
    Path(os.environ['ACONSOLE_NATIVE_STACK_PID']).write_text(str(pid)+' '+start)
    gdb.execute('continue',to_string=True)
    count=0
    while gdb.selected_inferior().pid:
        if os.path.exists(marker):
            gdb.execute('thread 1',to_string=True)
            stream.write('SAMPLE '+str(count)+'\n'+gdb.execute('bt 35',to_string=True)+'\n')
            stream.flush()
            count+=1
        gdb.execute('continue',to_string=True)
finally:
    stream.close()
end
