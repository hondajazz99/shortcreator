# short_creator.py
import os
import json
import logging
import random
import requests
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import moviepy.editor as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configuration and Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class Config:
    # Telegram
    TELEGRAM_TOKEN: str
    TELEGRAM_CHANNELS: List[str]
    
    # YouTube
    YOUTUBE_CLIENT_SECRETS: dict
    TITLE_TEMPLATE: str = "{channel} - {date}"
    DESCRIPTION: str = "Automated YouTube Short created from Telegram content"
    TAGS: List[str] = ["Shorts", "Auto-generated", "Telegram"]
    PRIVACY_STATUS: str = "private"  # private | public | unlisted
    
    # Content
    DURATION: int = 15  # seconds
    MUSIC_OPTION: str = "https://example.com/music.mp3"  # URL or "bundled"
    FONT_PATH: str = "Arial.ttf"
    OUTPUT_RESOLUTION: Tuple[int, int] = (1080, 1920)  # 9:16 aspect ratio

class TelegramClient:
    def __init__(self, token: str):
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()

    def get_latest_image(self, channel: str) -> Optional[Tuple[str, str]]:
        """Fetch latest image from Telegram channel with caption"""
        try:
            url = f"{self.base_url}getChat?chat_id={channel.lstrip('@')}"
            chat_info = self.session.get(url).json()
            if not chat_info["ok"]:
                logger.error(f"Failed to get chat info: {chat_info}")
                return None

            url = f"{self.base_url}getUpdates?allowed_updates=message"
            updates = self.session.get(url).json()
            for update in updates["result"]:
                if "message" in update and "photo" in update["message"]:
                    msg = update["message"]
                    if msg.get("chat", {}).get("id") == chat_info["result"]["id"]:
                        photo = max(msg["photo"], key=lambda x: x["file_id"])
                        file_url = f"{self.base_url}getFile?file_id={photo['file_id']}"
                        file_path = self.session.get(file_url).json()["result"]["file_path"]
                        caption = msg.get("caption", "No caption")
                        return f"https://api.telegram.org/file/bot{token}/{file_path}", caption
        except Exception as e:
            logger.error(f"Error fetching telegram content: {str(e)}")
        return None

class VideoCreator:
    def __init__(self, config: Config):
        self.config = config
        self.music_cache = Path(".music_cache")
        self.music_cache.mkdir(exist_ok=True)

    def download_music(self, url: str) -> Path:
        """Download music file with caching"""
        filename = self.music_cache / url.split("/")[-1]
        if not filename.exists():
            audio_data = requests.get(url).content
            filename.write_bytes(audio_data)
        return filename

    def create_short(self, image_url: str, caption: str) -> Path:
        """Create YouTube Short video from image and caption"""
        try:
            # Download and process image
            img_data = requests.get(image_url).content
            img_path = Path("temp_image.jpg")
            img_path.write_bytes(img_data)
            
            img = Image.open(img_path).resize(self.config.OUTPUT_RESOLUTION)
            
            # Create caption images with fade animation
            caption_images = self._generate_caption_images(caption, img.size)
            
            # Create video clips
            image_clip = mp.ImageClip(np.array(img), duration=self.config.DURATION)
            caption_clips = [mp.ImageClip(np.array(img), duration=0.05) for img in caption_images]
            video = mp.concatenate_videoclips([image_clip] + caption_clips)
            
            # Add music
            if self.config.MUSIC_OPTION:
                if self.config.MUSIC_OPTION.startswith("http"):
                    music_path = self.download_music(self.config.MUSIC_OPTION)
                else:
                    music_path = Path(self.config.MUSIC_OPTION)
                
                audio = mp.AudioFileClip(str(music_path))
                audio = audio.volumex(0.8)  # Normalize to -12 dB
                video = video.set_audio(audio)
            
            # Save video
            output_path = Path("output_short.mp4")
            video.write_videofile(
                str(output_path),
                fps=24,
                codec='libx264',
                audio_codec='aac',
                logger=logger
            )
            return output_path
        except Exception as e:
            logger.error(f"Video creation failed: {str(e)}")
            return None

    def _generate_caption_images(self, text: str, img_size: tuple) -> List[Image.Image]:
        """Generate caption images with fade effect"""
        fonts_dir = Path(__file__).parent / "fonts"
        try:
            font = ImageFont.truetype(self.config.FONT_PATH, 48)
        except:
            try:
                font = ImageFont.truetype(fonts_dir / "arial.ttf", 48)
            except:
                font = ImageFont.load_default()

        # Split text into lines
        words = text.split(" ")
        lines = []
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            if font.getlength(test_line) <= img_size[0] * 0.9:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word
        lines.append(current_line)

        # Create caption images
        caption_imgs = []
        for i, line in enumerate(lines):
            img = Image.new("RGBA", img_size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # Background rectangle
            text_width, text_height = draw.textsize(line, font=font)
            draw.rectangle(
                (10, img_size[1] - text_height - 60, img_size[0] - 10, img_size[1] - 20),
                fill=(0, 0, 0, 200)
            )
            
            # Text with opacity fade
            draw.text(
                (img_size[0]/2, img_size[1] - text_height - 40),
                line,
                font=font,
                fill=(255, 255, 255, min(255, (i + 1) * 50)),
                anchor="md"
            )
            caption_imgs.append(img)
        return caption_imgs

class YouTubeUploader:
    def __init__(self, credentials: dict):
        self.credentials = credentials

    def upload_short(self, video_path: Path, config: Config):
        """Upload video to YouTube Shorts"""
        try:
            creds = Credentials.from_authorized_user_info(self.credentials)
            youtube = build("youtube", "v3", credentials=creds)
            
            # Prepare metadata
            title = config.TITLE_TEMPLATE.format(
                channel=random.choice(config.TELEGRAM_CHANNELS),
                date=datetime.now().strftime("%Y-%m-%d")
            )
            
            # Upload video
            body = {
                "snippet": {
                    "title": title,
                    "description": config.DESCRIPTION,
                    "tags": config.TAGS,
                    "categoryId": "22"  # Entertainment category
                },
                "status": {
                    "privacyStatus": config.PRIVACY_STATUS,
                    "selfDeclaredMadeForKids": False
                }
            }
            
            # Resumable upload
            media = MediaFileUpload(
                str(video_path),
                chunksize=-1,  # Let library decide
                resumable=True
            )
            
            request = youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media
            )
            
            # Progress logging
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Uploaded {int(status.progress() * 100)}%")
            
            logger.info(f"Video uploaded successfully: https://youtu.be/{response['id']}")
            return response
        except Exception as e:
            logger.error(f"Upload failed: {str(e)}")

def main():
    # Load configuration from environment
    config = Config(
        TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"),
        TELEGRAM_CHANNELS=json.loads(os.getenv("TELEGRAM_CHANNELS", '["@example"]')),
        YOUTUBE_CLIENT_SECRETS=json.loads(os.getenv("YOUTUBE_CLIENT_SECRETS", "{}")),
        TITLE_TEMPLATE=os.getenv("TITLE_TEMPLATE", "New Short - {date}"),
        DESCRIPTION=os.getenv("DESCRIPTION", "Automated YouTube Short"),
        TAGS=json.loads(os.getenv("TAGS", '["Shorts", "Auto-generated"]')),
        PRIVACY_STATUS=os.getenv("PRIVACY_STATUS", "private"),
        DURATION=int(os.getenv("DURATION", 15)),
        MUSIC_OPTION=os.getenv("MUSIC_OPTION", ""),
        FONT_PATH=os.getenv("FONT_PATH", "Arial.ttf")
    )

    # Fetch Telegram content
    telegram = TelegramClient(config.TELEGRAM_TOKEN)
    content = None
    for channel in config.TELEGRAM_CHANNELS:
        content = telegram.get_latest_image(channel)
        if content:
            image_url, caption = content
            break
    
    if not content:
        logger.error("No suitable content found in any channel")
        return

    # Create video
    creator = VideoCreator(config)
    video_path = creator.create_short(image_url, caption)
    
    if not video_path or not video_path.exists():
        logger.error("Video creation failed")
        return

    # Upload to YouTube
    uploader = YouTubeUploader(config.YOUTUBE_CLIENT_SECRETS)
    uploader.upload_short(video_path, config)

if __name__ == "__main__":
    main()
