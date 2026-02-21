---
name: robot-sdk-ref
description: Fetch the live robot SDK reference and getting-started guide from the robot API. Use when (1) writing new robot skill code, (2) unsure about available SDK methods, classes, or endpoints, (3) debugging API calls or getting unexpected responses, (4) starting a session where robot code will be written — always read the guide before writing robot code.
---

# Robot SDK Reference

Fetch these via shell (`web_fetch` blocks private IPs):

1. **Getting-started guide** (read first before writing any robot code):
   ```bash
   curl -s -L http://<ROBOT_IP>:8080/docs/guide/html
   ```

2. **SDK reference** (method signatures, available classes):
   ```bash
   curl -s -L http://<ROBOT_IP>:8080/code/sdk/markdown
   ```

Do not guess APIs or invent endpoints. If the guide shows how to do something, do it that way.
