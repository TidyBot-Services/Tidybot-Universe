---
summary: "Workspace template for HEARTBEAT.md"
read_when:
  - Bootstrapping a workspace manually
---

# HEARTBEAT.md

## TidybotArmy Maintenance

Check these periodically (not every heartbeat — rotate through them):

### Catalog Sync
- Fetch `catalog.json` from TidyBotArmy/wishlist
- Compare against completed skill repos in TidyBotArmy org
- If any skill repo exists but isn't in catalog → update catalog.json and push

### Wishlist Status
- Fetch `wishlist.json` from TidyBotArmy/wishlist  
- Check for items with status "building" that have been idle >1 hour
- Check for completed skills still marked "pending" or "building" → update to "done"

### Catalog Analysis (Orchestrator Only)
- Review catalog.json for skills that could be consolidated
- Check if multiple skills duplicate code that should be a shared dependency
- If refactoring needed: plan extraction, update dependent skills' deps.txt, update catalog

### Sub-Agent Cleanup
- List sub-agent sessions idle >12 hours
- Report to user for confirmation before cleanup

---

# If nothing above needs attention, reply HEARTBEAT_OK.
