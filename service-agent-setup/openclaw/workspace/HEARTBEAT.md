# HEARTBEAT.md

Check these periodically (not every heartbeat — rotate through them):

### Service Health
- Query the deploy-agent: `GET http://<compute-node>:9000/services`
- Check that all expected services show `"status": "healthy"`
- If a service is unhealthy, check container logs or attempt redeploy

### GPU Status
- Query GPU info: `GET http://<compute-node>:9000/gpus`
- Check VRAM usage — are any GPUs running out of memory?
- Check that services are assigned to the correct GPUs

### Deploy-Agent Health
- Check the deploy-agent itself: `GET http://<compute-node>:9000/health`
- If it's unreachable, the compute node may need attention (SSH in to investigate)

---

# If nothing above needs attention, reply HEARTBEAT_OK.
