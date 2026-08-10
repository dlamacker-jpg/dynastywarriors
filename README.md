# CFB Dynasty — Discord server setup

One script that builds out all the roles, categories, and channels for an NCAA
College Football online dynasty. Edit `server_config.json`, run once, done.

## 1. Create the (empty) server
In Discord: **+** (left sidebar) → **Create My Own** → name it. That's the only
manual click — a bot can't create the server itself, but it can build everything
inside it.

## 2. Make a bot
1. Go to the [Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. Left menu → **Bot** → **Add Bot** → **Reset Token** → copy the token (this is your `DISCORD_BOT_TOKEN`).
   - Treat the token like a password. Don't paste it into chats or commit it.
3. Left menu → **OAuth2** → **URL Generator**:
   - Scopes: **bot**
   - Bot Permissions: **Administrator** (simplest for setup; you can lower it later)
   - Copy the generated URL, open it, and add the bot to your server.

## 3. Get your server (guild) ID
Discord → **User Settings → Advanced → Developer Mode** ON. Then right-click your
server icon → **Copy Server ID** (this is your `DISCORD_GUILD_ID`).

## 4. Run it
```bash
pip install "discord.py>=2.3"
export DISCORD_BOT_TOKEN="paste-token-here"
export DISCORD_GUILD_ID="paste-server-id-here"
python setup_server.py
```

Re-running is safe: anything that already exists (matched by name) is skipped, so
you can tweak `server_config.json` and run again to add new stuff.

## Customizing
Everything lives in `server_config.json`:
- **roles** — name, color (hex), `hoist` (show separately in member list), `admin` (full perms).
- **categories** → **channels** — `type` is `text` or `voice`; text channels take an optional `topic`.
- Add `"staff_only": true` to a category to hide it from everyone except Commissioner/Co-Commissioner.
