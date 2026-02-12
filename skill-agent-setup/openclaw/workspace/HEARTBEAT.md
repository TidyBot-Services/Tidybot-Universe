---
summary: "Workspace template for HEARTBEAT.md"
read_when:
  - Bootstrapping a workspace manually
---

# HEARTBEAT.md

Check these periodically (not every heartbeat — rotate through them):

### Practice In-Development Skills
- If you have any skills in progress in your local workspace, run a practice session
- Log results to your daily memory file
- Focus on improving reliability and handling edge cases

### Publishing Readiness
- Check locally-developed skills — have any accumulated enough successful test runs to suggest publishing?
- If a skill has 10+ successful unsupervised tests, ask the user if they'd like to publish it

### Catalog Sync
- Fetch `catalog.json` from tidybot-skills/wishlist
- Compare against completed skill repos in tidybot-skills org
- If any skill repo exists but isn't in catalog → update catalog.json and push

### Wishlist Check
- Fetch `wishlist.json` from tidybot-skills/wishlist
- Are there unclaimed items the agent could start exploring?
- Check for items with status "building" that have been idle >1 hour
- Check for completed skills still marked "pending" or "building" → update to "done"

---

# If nothing above needs attention, reply HEARTBEAT_OK.
