# README.md
# YouTube Shorts Automation

Automatically create YouTube Shorts from Telegram channel content.

## Setup

1. **Get Telegram API Token:**
   - Create bot via @BotFather
   - Get HTTP API token

2. **YouTube API Setup:**
   - Go to [Google API Console](https://console.cloud.google.com/apis/dashboard)
   - Create project
   - Enable YouTube Data API v3
   - Create OAuth 2.0 credentials
   - Download `client_secrets.json`

3. **Prepare Music:**
   - Provide music file path or URL to permissively licensed music
   - Music will be cached locally

4. **Set Environment Variables:**
   ```bash
   export TELEGRAM_TOKEN="your_telegram_api_token"
   export TELEGRAM_CHANNELS='["@channel1", "@channel2"]'
   export YOUTUBE_CLIENT_SECRETS='{"client_id": "...", "client_secret": "...", "refresh_token": "..."}'
   export TITLE_TEMPLATE="Your Title Template"
   export DESCRIPTION="Video description"
   export TAGS='["tag1", "tag2"]'
   export PRIVACY_STATUS="private"
   export DURATION=15
   export MUSIC_OPTION="https://example.com/music.mp3"
   export FONT_PATH="path/to/custom/font.ttf"
RUN

pip install -r requirements.txt
python short_creator.py

GITHUB ACTIONS EXAMPLE
name: Create YouTube Short

on:
  schedule:
    - cron: '0 12 * * *'  # Daily at noon
  workflow_dispatch:

jobs:
  build-and-upload:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4
        
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.8'
        
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
        
      - name: Run automation
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHANNELS: ${{ secrets.TELEGRAM_CHANNELS }}
          YOUTUBE_CLIENT_SECRETS: ${{ secrets.YOUTUBE_CLIENT_SECRETS }}
          # Add other env vars as needed
        run: |
          python short_creator.py

MUSIC REQUIREMENTS
Must be royalty-free with proper licensing
Supported sources:
Bundle music file with license documentation
Provide URL to permissively licensed music


This implementation provides:

1. **Telegram Integration:** Fetches images and captions from specified channels
2. **Video Creation:** Generates 9:16 vertical videos with animated captions
3. **Audio Handling:** Adds properly licensed background music
4. **YouTube Upload:** Resumable uploads with progress tracking
5. **Configuration:** Flexible parameters via environment variables
6. **Error Handling:** Comprehensive logging and error recovery
7. **CI/CD Ready:** Designed for automated execution in GitHub Actions

To use this system:

1. Set up your credentials and secrets
2. Configure your preferred settings in environment variables
3. Run the script manually or via scheduled CI/CD
4. Monitor the logs for execution details and troubleshooting

The script will automatically:
- Fetch the latest suitable content from your Telegram channels
- Create an optimized YouTube Short with caption animations
- Add properly licensed background music
- Upload directly to YouTube with your configured metadata
- Handle all temporary file cleanup
