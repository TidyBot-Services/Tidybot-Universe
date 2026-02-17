# HEARTBEAT.md

Check these periodically (not every heartbeat — rotate through them):

### Service Health
- Check systemd status of all `tidybot-*` services
- Hit each service's `/health` endpoint
- If any service is down, attempt restart and log the issue

### Wishlist Monitor
- Fetch `wishlist.json` from `TidyBot-Services/services_wishlist` via `gh api`
- Are there new items with status `pending`? Claim and build them
- Are there items stuck in `building`? Check if a sub-agent is working on them

### Catalog Accuracy
- Verify `catalog.json` entries match actually running services
- Remove or flag entries for services that are no longer deployed

---

# If nothing above needs attention, reply HEARTBEAT_OK.
