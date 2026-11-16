FROM 133aec7ab1411181567fef7fc3f4512123e1f9660090b49851de4b698b4ff772
RUN ["/usr/sbin/useradd", "--uid", "1000", "--user-group", "--create-home", "--shell", "/bin/sh", "sas"]
COPY --chown=1000:1000 rootfs/ /
COPY manifest.json /opt/aconsole/manifest.json
LABEL aconsole.gate.schema=1 aconsole.gate.tools-sha256=f8ce23b96c07078da50b71f04fa1b96fa563f3a6ddd64f564ac591599ab2c649
ENV PATH=/opt/aconsole/bin:/opt/aconsole/core/bin:/usr/local/bin:/usr/bin:/bin
USER sas
CMD ["/usr/bin/sleep", "infinity"]
