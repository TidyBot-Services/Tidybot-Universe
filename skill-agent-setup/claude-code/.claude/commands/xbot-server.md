# /xbot-server — Set up remote service server access

Initialize the service catalog to discover ML services on a remote GPU server.

## Usage

```
/xbot-server
```

## What to do

1. **Ask the user for server configuration.** Use AskUserQuestion to collect:

   - **Server IP** — the remote GPU server address (e.g., `192.168.1.100`)
   - **SSH username** — the user account on the server (e.g., `gpu_user`)
   - **Service directory** — the directory on the server containing deployed services (e.g., `/home/gpu_user/services`)

   Ask all three in one AskUserQuestion call. Do NOT hardcode or assume any values.

2. **Set up SSH access.** Test connectivity:

   ```bash
   ssh -o ConnectTimeout=5 -o BatchMode=yes USERNAME@SERVER_IP "echo connected"
   ```

   If it fails, ask the user to run `ssh-copy-id USERNAME@SERVER_IP` interactively:
   ```
   ! ssh-copy-id USERNAME@SERVER_IP
   ```

   Then verify again.

3. **Add SSH config alias** (if not already present). Add an entry to `~/.ssh/config`:

   ```
   Host service-server
       HostName SERVER_IP
       User USERNAME
       IdentityFile ~/.ssh/id_ed25519
   ```

4. **Run the setup script.** Execute:

   ```bash
   cd ~/tidybot_uni/Tidybot-Universe/service-server-setup
   bash setup.sh --server-ip SERVER_IP --username USERNAME --service-dir SERVICE_DIR
   ```

   This will:
   - Verify SSH connectivity
   - Verify the service directory exists
   - Write `config.json`
   - Run an initial scan to discover services

5. **Start the catalog server.** Launch in the background:

   ```bash
   cd ~/tidybot_uni/Tidybot-Universe/service-server-setup
   python3 service_scanner.py &
   ```

   Wait for it to be ready:
   ```bash
   sleep 10 && curl -s http://localhost:8090/health
   ```

6. **Show discovered services.** Fetch and display the catalog:

   ```bash
   curl -s http://localhost:8090/services
   ```

   Format the output as a clear table for the user showing:
   - Service name
   - Port
   - Status (running/stopped)
   - URL
   - Number of endpoints

7. **Report to user.** Tell them:
   - The catalog is live at `http://localhost:8090/docs/html`
   - It auto-refreshes every 30 seconds
   - Skill agents can query `curl localhost:8090/services` to discover services
   - If the server goes down, the catalog will alert after 3 failed checks
   - To force a rescan: `curl -X POST localhost:8090/rescan`

## If the catalog is already running

Check first:
```bash
curl -s http://localhost:8090/health
```

If it returns a valid response, skip setup and just show the current services.
If the user wants to reconfigure (different server), kill the old process and re-run setup.
