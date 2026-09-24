# Retired service catalog scanner

The former `service_scanner.py` daemon (port 8090) is retired. It was an
unused SSH-based catalog process; the independently maintained
[deploy-agent](https://github.com/TidyBot-Services/deploy-agent) provides the
active service discovery and lifecycle API on compute nodes (port 9000).

Do not start the old scanner or use this directory as a deployable service.
Its source and setup script remain recoverable from Git history before this
retirement. Service definitions belong in their respective service repositories;
the shared capability catalog belongs in
[services_wishlist](https://github.com/TidyBot-Services/services_wishlist).
