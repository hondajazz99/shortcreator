# YouTube Shorts Automation

Automatically create YouTube Shorts from Telegram channel content.

---

## 🔑 Getting a New OAuth Refresh Token (Required for Playlist Support)

Playlist management requires the **full YouTube scope**. If you previously generated a token with only `youtube.upload`, you must get a new one. Follow these exact steps:

### Step-by-step via OAuth Playground

1. Go to **https://developers.google.com/oauthplayground/**

2. Click the **gear icon ⚙️** (top-right) → check **"Use your own OAuth credentials"**
   - Enter your **OAuth Client ID** and **OAuth Client Secret** from Google Cloud Console

3. In the left panel, find **"YouTube Data API v3"** and select this scope:
   ```
   https://www.googleapis.com/auth/youtube
   ```
   > ⚠️ Do NOT use `youtube.upload` — that scope alone blocks playlist operations.

4. Click **"Authorize APIs"** → sign in with your YouTube account → allow access

5. Click **"Exchange authorization code for tokens"**

6. Copy the **Refresh token** value shown

7. Build your `YOUTUBE_CLIENT_SECRETS` JSON secret (store in GitHub Secrets):
   ```json
   {
     "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
     "client_secret": "YOUR_CLIENT_SECRET",
     "refresh_token": "PASTE_NEW_REFRESH_TOKEN_HERE",
     "token_uri": "https://oauth2.googleapis.com/token"
   }
   ```

---

## Setup

### 1. Telegram Bot Token
- Create a bot via [@BotFather](https://t.me/BotFather)
- Add the bot as an **admin** to your channel(s)
- Copy the HTTP API token

### 2. YouTube API (Google Cloud Console)
- Go to [Google API Console](https://console.cloud.google.com/apis/dashboard)
- Create a project → enable **YouTube Data API v3**
- Create **OAuth 2.0 credentials** (type: Web application)
- Add `https://developers.google.com/oauthplayground` as an authorized redirect URI
- Download or note your Client ID + Secret

### 3. GitHub Secrets

| Secret | Value |
|--------|-------|
| `TELEGRAM_TOKEN` | Your Telegram bot token |
| `TELEGRAM_CHANNELS` | `["@yourchannel"]` (JSON array) |
| `YOUTUBE_CLIENT_SECRETS` | Full JSON with client_id, client_secret, refresh_token, token_uri |
| `PLAYLIST_ID` | `PL3B7UtjF3P8ya2XNvBX8fgKOoqsCza8dv` |

### 4. Optional env vars (with defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `PUBLISH_DELAY_HOURS` | `1` | Hours until scheduled publish |
| `BRAND_HASHTAGS` | `["cryptohieuqua","cryptohieu.com"]` | Always-on hashtags |
| `DURATION` | `15` | Minimum video duration (seconds) |
| `PRIVACY_STATUS` | `private` | Upload privacy (always private when scheduled) |
| `TAGS` | `["Shorts","Auto-generated"]` | Base YouTube tags |
| `DESCRIPTION` | `"Automated YouTube Short"` | Video description prefix |
| `MUSIC_OPTION` | *(built-in URL)* | Background music URL or file path |

---

## Features

- **Telegram → YouTube pipeline** — fetches latest image+caption from your channels
- **Duplicate prevention** — tracks processed message IDs in `.published_ids.json`; never uploads the same post twice
- **Vietnamese TTS** — reads caption aloud with `vi-VN-HoaiMyNeural` voice, appending *"Đừng quên đăng ký kênh..."*
- **Scheduled publish** — uploads as private, auto-publishes after 1 hour
- **Playlist auto-add** — every new video is added to your playlist immediately after upload
- **Brand hashtags** — `#cryptohieuqua #cryptohieu.com` on every video
- **Word-synced captions** — highlighted karaoke-style words over a Ken Burns zoom
- **Background music** — mixed at lower volume when TTS is present

---

## Run locally

```bash
pip install -r requirements.txt

export TELEGRAM_TOKEN="..."
export TELEGRAM_CHANNELS='["@yourchannel"]'
export YOUTUBE_CLIENT_SECRETS='{"client_id":"...","client_secret":"...","refresh_token":"...","token_uri":"https://oauth2.googleapis.com/token"}'
export PLAYLIST_ID="PL3B7UtjF3P8ya2XNvBX8fgKOoqsCza8dv"

python short_creator.py
```
