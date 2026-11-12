import os
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[2]
out=root/'artifacts/qa24'
out.mkdir(exist_ok=True)
label=sys.argv[1]
with (out/(label+'.log')).open('x') as log:
    result=subprocess.run([sys.executable,'-m','pytest','-o','addopts=','-q','--junitxml='+str(out/(label+'.xml')),*sys.argv[2:]],cwd=root,env=dict(os.environ,PYTHONPATH=str(root/'src'),GDK_BACKEND='x11',GTK_A11Y='test'),stdout=log,stderr=subprocess.STDOUT,timeout=60)
print(label,result.returncode)
print((out/(label+'.log')).read_text()[-9000:])
sys.exit(result.returncode)
