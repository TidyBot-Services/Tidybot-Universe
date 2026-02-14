---
summary: "Workspace template for HEARTBEAT.md"
read_when:
  - Bootstrapping a workspace manually
---

# HEARTBEAT.md

Check these periodically (not every heartbeat — rotate through them):

### Practice In-Development Skills
- Check your `dev/` folder for skills in progress
- For each dev skill: first look at the camera to verify the task is feasible given what the robot currently sees
- If feasible, practice a few trials (max 10 per skill per day). Record success/failure for each
- Accumulate stats across sessions — keep a running tally of total trials and successes per skill in your memory files
- **If the skill code has changed since last tally, reset stats to zero** and start fresh
- Write down findings — note consistent errors and suggestions, but **do not modify the skill code**. Just report what you observe
- If a skill reaches 70%+ cumulative success rate, it may be ready for publishing

### Publishing Readiness
- Check dev skills that have reached 70%+ success rate
- Ask the user if they'd like to publish: *"Skill X has a 70% success rate over N trials. Want me to publish it?"*

### Wishlist Check
- Fetch `wishlist.json` from tidybot-skills/wishlist
- Are there unclaimed items you could start exploring?
- Check for items with status "building" that have been idle — update if needed

---

# If nothing above needs attention, reply HEARTBEAT_OK.
