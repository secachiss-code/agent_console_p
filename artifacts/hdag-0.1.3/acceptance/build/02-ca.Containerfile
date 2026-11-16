FROM a8193fc911a7de4659971ee79cbf661a1a54efc6acf4316b8a8e7360a7c54502
COPY ca.crt /etc/ssl/certs/ca-certificates.crt
COPY ca.crt /etc/ssl/cert.pem
COPY manifest.json /opt/aconsole/manifest.json
