# short_creator.py
import os
import json
import logging
import random
import requests
import subprocess
import sys
from dataclasses import dataclass, field
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

def get_env_json(key: str, default: str = "{}") -> dict:
    """Safely get and parse JSON environment variables"""
    try:
        value = os.getenv(key)
        if not value:
            logger.warning(f"Using default value for {key}")
            return json.loads(default)
        return json.loads(value)
    except Exception as e:
        logger.error(f"Error parsing {key}: {str(e)}")
        return json.loads(default)

@dataclass
class Config:
    # Telegram
    TELEGRAM_TOKEN: str
    TELEGRAM_CHANNELS: List[str]
    
    # YouTube
    YOUTUBE_CLIENT_SECRETS: dict
    TITLE_TEMPLATE: str = "{channel} - {date}"
    DESCRIPTION: str = "Automated YouTube Short created from Telegram content"
    TAGS: List[str] = field(default_factory=lambda: ["Shorts", "Auto-generated", "Telegram"])
    PRIVACY_STATUS: str = "private"
    
    # Content
    DURATION: int = 15
    MUSIC_OPTION: str = "https://api.ttok.com/api/proxy?url=https%3A%2F%2Fcdn.pixabay.com%2Fdownload%2Faudio%2F2026%2F03%2F24%2Faudio_b3f7aa2696.mp3%3Ffilename%3Dthe_mountain-cheerful-cheerful-music-507997.mp3"
    FONT_PATH: str = "Arial.ttf"
    OUTPUT_RESOLUTION: Tuple[int, int] = field(default_factory=lambda: (1080, 1920))

class TelegramClient:
    def __init__(self, token: str):
        self.token = token  # ← was missing!
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()

    def get_latest_image(self, channel: str) -> Optional[Tuple[str, str]]:
        try:
            url = f"{self.base_url}getUpdates?allowed_updates=[\"channel_post\",\"message\"]"
            updates = self.session.get(url).json()
        
            logger.info(f"Total updates received: {len(updates.get('result', []))}")
        
            for update in reversed(updates.get("result", [])):
                post = update.get("channel_post") or update.get("message", {})
                sender = post.get("sender_chat", {}).get("username", "none")
                chat = post.get("chat", {}).get("username", "none")
                has_photo = "photo" in post
                logger.info(f"Update: sender_chat=@{sender}, chat=@{chat}, has_photo={has_photo}")
            
                chat_username = "@" + (
                    post.get("sender_chat", {}).get("username") or
                    post.get("chat", {}).get("username", "")
                )
                logger.info(f"Comparing: '{chat_username}' == '{channel}'")
            
                if chat_username == channel and has_photo:
                    photo = max(post["photo"], key=lambda x: x["file_size"])
                    file_resp = self.session.get(
                        f"{self.base_url}getFile?file_id={photo['file_id']}"
                    ).json()
                    file_path = file_resp["result"]["file_path"]
                    caption = post.get("caption", "No caption")
                    return (
                        f"https://api.telegram.org/file/bot{self.token}/{file_path}",
                        caption
                    )
        except Exception as e:
            logger.error(f"Error fetching telegram content: {str(e)}")
        return None

class VideoCreator:
    def __init__(self, config: Config):
        self.config = config
        self.music_cache = Path(".music_cache")
        self.music_cache.mkdir(exist_ok=True)
        self.fonts_dir = Path(__file__).parent / "fonts"
        self.fonts_dir.mkdir(exist_ok=True)

    def download_music(self, url: str) -> Path:
        try:
            filename = self.music_cache / url.split("/")[-1]
            if not filename.exists():
                logger.info(f"Downloading music from {url}")
                audio_data = requests.get(url).content
                filename.write_bytes(audio_data)
            return filename
        except Exception as e:
            logger.error(f"Music download failed: {str(e)}")
            return Path(self.music_cache / "default.mp3")

    def create_short(self, image_url: str, caption: str) -> Optional[Path]:
        try:
            # Download and process image
            img_data = requests.get(image_url).content
            img_path = Path("temp_image.jpg")
            img_path.write_bytes(img_data)
            img = Image.open(img_path).resize(self.config.OUTPUT_RESOLUTION)
            
            # Create caption images
            caption_images = self._generate_caption_images(caption, img.size)
            
            # Create video clips
            image_clip = mp.ImageClip(np.array(img), duration=self.config.DURATION)
            caption_clips = []
            for i, img in enumerate(caption_images):
                caption_clips.append(mp.ImageClip(np.array(img), duration=0.05))
            video = mp.concatenate_videoclips([image_clip] + caption_clips)
            
            # Add music
            if self.config.MUSIC_OPTION:
                if self.config.MUSIC_OPTION.startswith("http"):
                    music_path = self.download_music(self.config.MUSIC_OPTION)
                else:
                    music_path = Path(self.config.MUSIC_OPTION)
                
                audio = mp.AudioFileClip(str(music_path))
                audio = audio.volumex(0.8)
                video = video.set_audio(audio)
            
            # Save video
            output_path = Path("output_short.mp4")
            video.write_videofile(
                str(output_path),
                fps=24,
                codec='libx264',
                audio_codec='aac',
                logger="bar"  # MoviePy's built-in progress bar, or None to silence
            )
            return output_path
        except Exception as e:
            logger.error(f"Video creation failed: {str(e)}")
            return None
        finally:
            if img_path.exists():
                img_path.unlink()

    def _generate_caption_images(self, text: str, img_size: tuple) -> List[Image.Image]:
        try:
            # Encode text to handle unicode
            text = text.encode('utf-8').decode('utf-8')
            # Try fonts in order until one works
            font = None
            font_paths = [
                Path(self.config.FONT_PATH),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),      # Linux
                Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),  # Linux
                Path("/System/Library/Fonts/Helvetica.ttc"),                   # Mac
                Path("C:/Windows/Fonts/arial.ttf"),                            # Windows
                self.fonts_dir / "DejaVuSans.ttf",
            ]
        
            for path in font_paths:
                if path.exists():
                    try:
                        font = ImageFont.truetype(str(path), 48)
                        logger.info(f"Using font: {path}")
                        break
                    except:
                        continue
        
            if font is None:
                logger.warning("No Unicode font found, downloading DejaVuSans")
                font_url = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf"
                font_data = requests.get(font_url).content
                font_save_path = self.fonts_dir / "DejaVuSans.ttf"
                font_save_path.write_bytes(font_data)
                font = ImageFont.truetype(str(font_save_path), 48)

            # Split text into lines
            words = text.split(" ")
            lines = []
            test_line = ""
            
            for word in words:
                test_line += f"{word} "
                if font.getlength(test_line.strip()) <= img_size[0] * 0.9:
                    continue
                else:
                    lines.append(test_line.strip())
                    test_line = ""
            
            if test_line:
                lines.append(test_line.strip())

            # Create caption images with fade effect
            caption_imgs = []
            for i, line in enumerate(lines):
                img = Image.new("RGBA", img_size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                
                # Background rectangle
                #text_width, text_height = draw.textsize(line, font=font)
                bbox = draw.textbbox((0, 0), line, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                padding = 20
                draw.rectangle(
                    (padding, img_size[1] - text_height - padding - 40, 
                     img_size[0] - padding, img_size[1] - padding - 20),
                    fill=(0, 0, 0, 200)
                )
                
                # Text with opacity
                draw.text(
                    (img_size[0]/2, img_size[1] - text_height - 40 - 20),
                    line,
                    font=font,
                    fill=(255, 255, 255, min(255, (i + 1) * 60)),
                    anchor="md"
                )
                caption_imgs.append(img)
            return caption_imgs
        except Exception as e:
            logger.error(f"Caption generation failed: {str(e)}")
            return []

class YouTubeUploader:
    def __init__(self, credentials: dict):
        self.credentials = credentials

    def upload_short(self, video_path: Path, config: Config):
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
                    "categoryId": "22"
                },
                "status": {
                    "privacyStatus": config.PRIVACY_STATUS
                }
            }
            
            media = MediaFileUpload(
                str(video_path),
                chunksize=-1,
                resumable=True
            )
            
            request = youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media
            )
            
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Uploaded {int(status.progress() * 100)}%")
            
            logger.info(f"Video uploaded: https://youtu.be/{response['id']}")
            return response
        except Exception as e:
            logger.error(f"Upload failed: {str(e)}")

def main():
    try:
        # Load configuration
        config = Config(
            TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"),
            TELEGRAM_CHANNELS=get_env_json("TELEGRAM_CHANNELS", '["@TechTalk66"]'),
            YOUTUBE_CLIENT_SECRETS=get_env_json("YOUTUBE_CLIENT_SECRETS"),
            TITLE_TEMPLATE=os.getenv("TITLE_TEMPLATE", "New Short - {date}"),
            DESCRIPTION=os.getenv("DESCRIPTION", "Automated YouTube Short"),
            TAGS=get_env_json("TAGS", '["Shorts", "Auto-generated"]'),
            PRIVACY_STATUS=os.getenv("PRIVACY_STATUS", "private"),
            DURATION=int(os.getenv("DURATION", 45)),
            MUSIC_OPTION=os.getenv("MUSIC_OPTION", "https://api.ttok.com/api/proxy?url=https%3A%2F%2Fcdn.pixabay.com%2Fdownload%2Faudio%2F2026%2F03%2F24%2Faudio_b3f7aa2696.mp3%3Ffilename%3Dthe_mountain-cheerful-cheerful-music-507997.mp3"),
            FONT_PATH=os.getenv("FONT_PATH", "Arial.ttf")
        )

        # Validate configuration
        if not config.TELEGRAM_TOKEN:
            raise ValueError("TELEGRAM_TOKEN is required")
        if not config.YOUTUBE_CLIENT_SECRETS:
            raise ValueError("YOUTUBE_CLIENT_SECRETS must be configured")
        if not config.TELEGRAM_CHANNELS:
            raise ValueError("At least one TELEGRAM_CHANNEL must be specified")

        # Run process
        telegram = TelegramClient(config.TELEGRAM_TOKEN)
        content = None
        for channel in config.TELEGRAM_CHANNELS:
            content = telegram.get_latest_image(channel)
            if content:
                image_url, caption = content
                break
        
        if not content:
            logger.error("No suitable content found")
            return

        creator = VideoCreator(config)
        video_path = creator.create_short(image_url, caption)
        if not video_path or not video_path.exists():
            raise RuntimeError("Video creation failed")

        uploader = YouTubeUploader(config.YOUTUBE_CLIENT_SECRETS)
        uploader.upload_short(video_path, config)

        # Cleanup
        if video_path.exists():
            video_path.unlink()
            logger.info("Temporary files cleaned up")

    except Exception as e:
        logger.exception("Fatal error in main process")
        sys.exit(1)

if __name__ == "__main__":
    main()
