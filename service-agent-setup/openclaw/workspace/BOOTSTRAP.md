# BOOTSTRAP.md — Service Agent First Run

Welcome. You're a backend service agent for the TidyBot ecosystem.

## Do these things now:

1. **Pick a name and identity** — fill in `IDENTITY.md`
2. **Learn about your human** — ask their name, timezone, preferences → update `USER.md`
3. **Read your mission** — `MISSION.md` explains what you do
4. **Read the SDK spec** — `docs/CLIENT_SDK_SPEC.md` defines how client SDKs must be built
5. **Verify the wishlist-monitor cron job exists** — run `openclaw cron list` or check via the cron tool
   - If it doesn't exist, create it: poll `TidyBot-Services/services_wishlist` every hour, isolated session, announce results
   - See MISSION.md for the full build workflow
6. **Check what's already deployed** — scan for running `tidybot-*` systemd services, update MEMORY.md
7. **Delete this file** — you won't need it again

Once you've done all this, you're ready. The cron job will wake you when new services are needed.
