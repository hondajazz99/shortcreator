# short_creator.py
import asyncio
import os
import json
import logging
import random
import requests
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Union

import moviepy.editor as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configuration and Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_env_json(key: str, default: str = "[]") -> Union[list, dict]:
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
    TITLE_TEMPLATE: str = "Video Short - {date}"
    DESCRIPTION: str = "Automated YouTube Short created from Telegram content"
    TAGS: List[str] = field(default_factory=lambda: ["Shorts", "Auto-generated", "Telegram"])
    PRIVACY_STATUS: str = "private"

    # Content
    DURATION: int = 15
    MUSIC_OPTION: str = "https://api.ttok.com/api/proxy?url=https%3A%2F%2Fcdn.pixabay.com%2Fdownload%2Faudio%2F2026%2F03%2F24%2Faudio_b3f7aa2696.mp3%3Ffilename%3Dthe_mountain-cheerful-cheerful-music-507997.mp3"
    FONT_PATH: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_BOLD_PATH: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    OUTPUT_RESOLUTION: Tuple[int, int] = field(default_factory=lambda: (1080, 1920))


class TelegramClient:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()

    def get_latest_image(self, channel: str) -> Optional[Tuple[str, str]]:
        try:
            url = f"{self.base_url}getUpdates?allowed_updates=[\"channel_post\",\"message\"]"
            updates = self.session.get(url).json()

            logger.info(f"Total updates received: {len(updates.get('result', []))}")

            if not updates["ok"]:
                logger.error(f"Failed to get updates: {updates}")
                return None

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

    def _fit_image(self, img: Image.Image, target_size: tuple) -> Image.Image:
        """Crop and resize image to exactly fill target size (like CSS object-fit: cover)"""
        target_w, target_h = target_size
        orig_w, orig_h = img.size

        scale = max(target_w / orig_w, target_h / orig_h)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)

        img = img.resize((new_w, new_h), Image.LANCZOS)

        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        img = img.crop((left, top, left + target_w, top + target_h))

        return img

    async def _generate_tts(self, text: str) -> Tuple[Optional[Path], list]:
        try:
            import edge_tts
            tts_path = Path("temp_tts.mp3")
            word_timings = []

            communicate = edge_tts.Communicate(text, voice="vi-VN-HoaiMyNeural")

            with open(str(tts_path), "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        word_timings.append({
                            "word": chunk["text"],
                            "start": chunk["offset"] / 10_000_000,
                            "duration": chunk["duration"] / 10_000_000
                        })

            # Get real TTS duration from the generated file
            raw_audio = mp.AudioFileClip(str(tts_path))
            real_duration = raw_audio.duration
            raw_audio.close()
            logger.info(f"TTS real duration: {real_duration:.2f}s")

            # Vietnamese doesn't fire WordBoundary — build even-split from real duration
            if not word_timings:
                logger.warning("No WordBoundary events — using even split based on real duration")
                words = text.split()
                per_word = (real_duration / 1.25) / max(len(words), 1)  # account for x1.25 speed
                word_timings = [
                    {
                        "word": word,
                        "start": round(i * per_word, 3),
                        "duration": round(per_word * 0.85, 3)
                    }
                    for i, word in enumerate(words)
                ]
                logger.info(f"Built {len(word_timings)} even-split timings, {per_word:.2f}s per word")

            return tts_path, word_timings
        except Exception as e:
            logger.error(f"TTS generation failed: {str(e)}")
            return None, []


    def _generate_caption_frame(self, text: str, highlight_word: str, img: Image.Image) -> np.ndarray:
        frame = img.copy().convert("RGBA")
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font = ImageFont.truetype(self.config.FONT_PATH, 52)
        font_bold = ImageFont.truetype(self.config.FONT_BOLD_PATH, 62)
    
        # Emoji font fallback
        emoji_font_paths = [
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "/usr/share/fonts/noto/NotoColorEmoji.ttf",
            "/usr/share/fonts/truetype/noto-color-emoji/NotoColorEmoji.ttf",
        ]
        emoji_font = None
        for path in emoji_font_paths:
            if Path(path).exists():
                try:
                    emoji_font = ImageFont.truetype(path, 109)  # NotoColorEmoji needs size 109
                    logger.info(f"Emoji font loaded: {path}")
                    break
                except Exception:
                    continue
        if not emoji_font:
            logger.warning("Emoji font not found, emojis may not render")

        def is_emoji(char: str) -> bool:
            cp = ord(char)
            return (
            0x1F300 <= cp <= 0x1FABF or  # misc symbols, emoticons
            0x1F600 <= cp <= 0x1F64F or  # emoticons
            0x1F680 <= cp <= 0x1F6FF or  # transport
            0x2600  <= cp <= 0x26FF  or  # misc symbols
            0x2700  <= cp <= 0x27BF  or  # dingbats
            0xFE00  <= cp <= 0xFE0F  or  # variation selectors
            0x1F900 <= cp <= 0x1F9FF or  # supplemental symbols
            0x1FA00 <= cp <= 0x1FA6F or  # chess symbols
            cp == 0x200D               or  # zero width joiner
            0x1F1E0 <= cp <= 0x1F1FF     # flags
            )

    def draw_word_with_emoji(draw, x, y, word, font, color):
        """Draw word char by char, switching to emoji font when needed"""
        cursor_x = x
        i = 0
        while i < len(word):
            char = word[i]
            # Handle multi-char emoji sequences (e.g. flags, ZWJ sequences)
            seq = char
            while i + len(seq) < len(word) and (
                is_emoji(word[i + len(seq)]) or ord(word[i + len(seq)]) == 0x200D
            ):
                seq += word[i + len(seq)]
            
            if is_emoji(char) and emoji_font:
                # Draw emoji
                draw.text((cursor_x, y - 10), seq, font=emoji_font, embedded_color=True)
                bbox = emoji_font.getbbox(seq)
                cursor_x += (bbox[2] - bbox[0]) + 4
                i += len(seq)
            else:
                draw.text((cursor_x + 2, y + 2), char, font=font, fill=(0, 0, 0, 180))  # shadow
                draw.text((cursor_x, y), char, font=font, fill=color)
                bbox = font.getbbox(char)
                cursor_x += (bbox[2] - bbox[0])
                i += 1
        return cursor_x

    def measure_word(word, font):
        """Measure word width accounting for emojis"""
        width = 0
        for char in word:
            if is_emoji(char) and emoji_font:
                bbox = emoji_font.getbbox(char)
                width += (bbox[2] - bbox[0]) + 4
            else:
                bbox = font.getbbox(char)
                width += (bbox[2] - bbox[0])
        return width

        words = text.split()
        img_w, img_h = frame.size
        padding = 24
        line_height = 75
        max_width = img_w * 0.85

        # Word wrap
        lines = []
        current_line = []
        current_width = 0
        for word in words:
            word_w = measure_word(word + " ", font)
            if current_width + word_w > max_width and current_line:
                lines.append(current_line)
                current_line = [word]
                current_width = word_w
            else:
                current_line.append(word)
                current_width += word_w
        if current_line:
            lines.append(current_line)

        total_height = line_height * len(lines) + padding * 2
        block_top = img_h - total_height - padding * 2
        block_bottom = img_h - padding

        # Background
        draw.rectangle(
            (padding, block_top, img_w - padding, block_bottom),
            fill=(0, 0, 0, 200)
        )

        # Draw words
        for line_idx, line_words in enumerate(lines):
            # Measure full line width for centering
            line_w = sum(measure_word(w + " ", font) for w in line_words)
            x = (img_w - line_w) // 2
            y = block_top + padding + line_idx * line_height

        for word in line_words:
            clean_word = word.lower().strip(".,!?:;\"'")
            clean_highlight = highlight_word.lower().strip(".,!?:;\"'")
            is_highlight = clean_word == clean_highlight and highlight_word != ""

            current_font = font_bold if is_highlight else font

            if is_highlight:
                wb = (x - 4, y - 2, x + measure_word(word, current_font) + 4, y + line_height - 10)
                draw.rectangle(wb, fill=(255, 200, 0, 220))
                color = (0, 0, 0, 255)
            else:
                color = (255, 255, 255, 255)

            next_x = draw_word_with_emoji(draw, x, y, word, current_font, color)
            x = next_x + measure_word(" ", font)

        result = Image.alpha_composite(frame, overlay)
        return np.array(result.convert("RGB"))

    async def create_short(self, image_url: str, caption: str) -> Optional[Path]:
        img_path = Path("temp_image.jpg")
        tts_path = Path("temp_tts.mp3")
        tts_fast_path = Path("temp_tts_fast.mp3")
        try:
            # Download and process image
            img_data = requests.get(image_url).content
            img_path.write_bytes(img_data)
            img = Image.open(img_path)
            img = self._fit_image(img, self.config.OUTPUT_RESOLUTION)

            # Generate TTS with word timings
            tts_path, word_timings = await self._generate_tts(caption)
            logger.info(f"Word timings received: {word_timings[:5]}")  # log first 5 words
            tts_audio = None

            if tts_path and tts_path.exists():
                # Speed up TTS to x1.25
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(tts_path),
                    "-filter:a", "atempo=1.25",
                    str(tts_fast_path)
                ], check=True, capture_output=True)
                tts_audio = mp.AudioFileClip(str(tts_fast_path))

                # Adjust word timings for x1.25 speed
                #word_timings = [{
                #    "word": w["word"],
                #    "start": w["start"] / 1.25,
                #    "duration": w["duration"] / 1.25
                #} for w in word_timings]

            tts_duration = tts_audio.duration if tts_audio else 0
            video_duration = max(self.config.DURATION, tts_duration + 1.0)

            # Generate frames with synced captions
            fps = 24
            total_frames = int(video_duration * fps)
            frames = []

            logger.info(f"Generating {total_frames} synced caption frames...")
            for frame_idx in range(total_frames):
                current_time = frame_idx / fps

                # Find current word being spoken
                current_word = ""
                for timing in word_timings:
                    if timing["start"] <= current_time <= timing["start"] + timing["duration"]:
                        current_word = timing["word"]
                        break

                frame = self._generate_caption_frame(caption, current_word, img)
                frames.append(frame)

            # Create video from frames
            video = mp.ImageSequenceClip(frames, fps=fps)
            video = video.fadein(0.5).fadeout(0.5)

            # Mix background music + TTS
            if self.config.MUSIC_OPTION:
                if self.config.MUSIC_OPTION.startswith("http"):
                    music_path = self.download_music(self.config.MUSIC_OPTION)
                else:
                    music_path = Path(self.config.MUSIC_OPTION)

                bg_audio = mp.AudioFileClip(str(music_path))

                # Loop music if shorter than video duration
                if bg_audio.duration < video_duration:
                    loops = int(video_duration / bg_audio.duration) + 1
                    bg_audio = mp.concatenate_audioclips([bg_audio] * loops)
                bg_audio = bg_audio.subclip(0, video_duration)
                bg_audio = bg_audio.audio_fadein(1.0).audio_fadeout(1.5)

                # Lower bg music when TTS is present
                bg_volume = 0.3 if tts_audio else 0.8
                bg_audio = bg_audio.volumex(bg_volume)

                if tts_audio:
                    final_audio = mp.CompositeAudioClip([bg_audio, tts_audio.volumex(1.0)])
                else:
                    final_audio = bg_audio

                video = video.set_audio(final_audio)
            elif tts_audio:
                video = video.set_audio(tts_audio)

            # Save video
            output_path = Path("output_short.mp4")
            video.write_videofile(
                str(output_path),
                fps=fps,
                codec='libx264',
                audio_codec='aac',
                logger="bar"
            )
            return output_path

        except Exception as e:
            logger.error(f"Video creation failed: {str(e)}")
            return None
        finally:
            for f in [img_path, tts_path, tts_fast_path]:
                if Path(f).exists():
                    Path(f).unlink()


class YouTubeUploader:
    def __init__(self, credentials: dict):
        self.credentials = credentials

    def upload_short(self, video_path: Path, config: Config, caption: str = ""):
        try:
            creds = Credentials.from_authorized_user_info(self.credentials)
            youtube = build("youtube", "v3", credentials=creds)

            # Generate hashtags from caption words
            caption_tags = [
                word.strip("#.,!?").lower()
                for word in caption.split()
                if len(word.strip("#.,!?")) > 3
            ]
            all_tags = config.TAGS + caption_tags
            hashtags = " ".join(f"#{tag}" for tag in caption_tags[:5])

            # Title: "Video Short + caption + datetime"
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            title = f"Video Short {caption[:50]} {date_str}"

            # Description with hashtags
            description = f"{config.DESCRIPTION}\n\n{hashtags}\n#Shorts"

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": all_tags,
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


async def _main():
    try:
        # Load configuration
        config = Config(
            TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"),
            TELEGRAM_CHANNELS=get_env_json("TELEGRAM_CHANNELS", '["@example"]'),
            YOUTUBE_CLIENT_SECRETS=get_env_json("YOUTUBE_CLIENT_SECRETS", '{}'),
            TITLE_TEMPLATE=os.getenv("TITLE_TEMPLATE", "Video Short - {date}"),
            DESCRIPTION=os.getenv("DESCRIPTION", "Automated YouTube Short"),
            TAGS=get_env_json("TAGS", '["Shorts", "Auto-generated"]'),
            PRIVACY_STATUS=os.getenv("PRIVACY_STATUS", "private"),
            DURATION=int(os.getenv("DURATION", 15)),
            MUSIC_OPTION=os.getenv("MUSIC_OPTION", "https://api.ttok.com/api/proxy?url=https%3A%2F%2Fcdn.pixabay.com%2Fdownload%2Faudio%2F2026%2F03%2F24%2Faudio_b3f7aa2696.mp3%3Ffilename%3Dthe_mountain-cheerful-cheerful-music-507997.mp3"),
            FONT_PATH=os.getenv("FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            FONT_BOLD_PATH=os.getenv("FONT_BOLD_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        )

        # Validate configuration
        if not config.TELEGRAM_TOKEN:
            raise ValueError("TELEGRAM_TOKEN is required")
        if not config.YOUTUBE_CLIENT_SECRETS:
            raise ValueError("YOUTUBE_CLIENT_SECRETS must be configured")
        if not config.TELEGRAM_CHANNELS:
            raise ValueError("At least one TELEGRAM_CHANNEL must be specified")

        # Fetch content from Telegram
        telegram = TelegramClient(config.TELEGRAM_TOKEN)
        content = None
        caption = ""
        image_url = ""

        for channel in config.TELEGRAM_CHANNELS:
            content = telegram.get_latest_image(channel)
            if content:
                image_url, caption = content
                break

        if not content:
            logger.error("No suitable content found")
            return

        # Create video
        creator = VideoCreator(config)
        video_path = await creator.create_short(image_url, caption)
        if not video_path or not video_path.exists():
            raise RuntimeError("Video creation failed")

        # Upload to YouTube
        uploader = YouTubeUploader(config.YOUTUBE_CLIENT_SECRETS)
        uploader.upload_short(video_path, config, caption=caption)

        # Cleanup
        if video_path.exists():
            video_path.unlink()
            logger.info("Temporary files cleaned up")

    except Exception as e:
        logger.exception("Fatal error in main process")
        sys.exit(1)


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
