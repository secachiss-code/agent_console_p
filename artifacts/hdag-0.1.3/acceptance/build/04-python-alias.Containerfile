FROM daa00c401203fc0e59bd55570b16b4c05b44b84e386289b8597a69461cd54435
USER root
RUN ["ln", "-s", "/opt/aconsole/core/bin/python", "/usr/bin/python3"]
USER sas
