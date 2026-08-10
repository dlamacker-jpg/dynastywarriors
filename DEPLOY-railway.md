# Deploy the welcome bot to Railway (always-on)

The bot runs 24/7 on Railway so new members get greeted even when your Mac is
off. Your token is **never** in these files — you'll paste it into Railway's
secret settings, and `.gitignore` keeps secrets out of git.

## What runs
Railway runs `welcome_bot.py` continuously (see `railway.json`), auto-restarting
if it ever crashes. The one-shot scripts (`setup_server.py`, `post_rules.py`) are
NOT run on Railway — you run those from your Mac only when you need them.

## Steps

### 1. Put this folder on GitHub
From the `dynasty-discord` folder:
```bash
git init
git add .
git commit -m "Dynasty Warriors Discord bot"
```
Create a new repo on GitHub (private is fine), then:
```bash
git remote add origin https://github.com/YOUR_USERNAME/dynasty-discord.git
git branch -M main
git push -u origin main
```

### 2. Create the Railway project
1. Go to https://railway.app and sign in (with GitHub is easiest).
2. **New Project** -> **Deploy from GitHub repo** -> pick `dynasty-discord`.
3. Railway auto-detects Python and installs `requirements.txt`.

### 3. Add your secrets (this is where the token goes)
In the Railway project -> **Variables** tab -> add:
- `DISCORD_BOT_TOKEN` = your bot token (the fresh one)
- `DISCORD_GUILD_ID` = your server ID

Railway stores these encrypted. Never put them in the code or the repo.

### 4. Deploy
Railway deploys automatically. Open the **Deploy Logs** — you should see:
```
Welcome bot online as DynastyWarriors#XXXX. Watching for new members...
```
That's it — it's live 24/7. Every push to GitHub redeploys automatically.

## Cost
Railway gives trial credit to start; after that a tiny always-on bot like this
runs around ~$5/month. You can watch usage in the Railway dashboard.

## Updating the welcome message later
Edit `welcome_bot.py`, then:
```bash
git add welcome_bot.py && git commit -m "tweak welcome" && git push
```
Railway redeploys on its own.
