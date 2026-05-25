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
from datetime import datetime, timezone, timedelta, timezone, timedelta
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
    PLAYLIST_ID: str = "PLKfhqWP2rL8LS6mS4eJk0sx43sD4x8TeV"  # Target playlist
    PUBLISH_DELAY_HOURS: int = 1                               # Schedule publish N hours from now
    BRAND_HASHTAGS: List[str] = field(default_factory=lambda: ["xeonbit24", "xeonbit24.com"])

    # Content
    DURATION: int = 15
    MUSIC_OPTION: str = "music.mp3"
    FONT_PATH: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_BOLD_PATH: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    OUTPUT_RESOLUTION: Tuple[int, int] = field(default_factory=lambda: (1080, 1920))
    LOGO_PATH: str = "brand_logo.png"
    PUBLISHED_IDS_FILE: str = ".published_ids.json"  # Tracks processed Telegram message IDs


class TelegramClient:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()

    def get_latest_images(
        self, channel: str, published_ids: set, max_posts: int = 10
    ) -> List[Tuple[str, str, str]]:
        """Return a list of (image_url, caption, unique_key) for up to *max_posts*
        unprocessed photo posts from *channel*, newest-first."""
        results: List[Tuple[str, str, str]] = []
        try:
            url = f"{self.base_url}getUpdates?allowed_updates=[\"channel_post\",\"message\"]"
            updates = self.session.get(url).json()
            logger.info(f"Total updates received: {len(updates.get('result', []))}")
            if not updates["ok"]:
                logger.error(f"Failed to get updates: {updates}")
                return results
            for update in reversed(updates.get("result", [])):
                if len(results) >= max_posts:
                    break
                post = update.get("channel_post") or update.get("message", {})
                sender = post.get("sender_chat", {}).get("username", "none")
                chat_obj = post.get("chat", {}).get("username", "none")
                has_photo = "photo" in post
                logger.info(f"Update: sender_chat=@{sender}, chat=@{chat_obj}, has_photo={has_photo}")
                chat_username = "@" + (
                    post.get("sender_chat", {}).get("username") or
                    post.get("chat", {}).get("username", "")
                )
                logger.info(f"Comparing: '{chat_username}' == '{channel}'")
                if chat_username == channel and has_photo:
                    message_id = str(post.get("message_id", update.get("update_id", "")))
                    unique_key = f"{channel}:{message_id}"
                    if unique_key in published_ids:
                        logger.info(f"Skipping already-published post: {unique_key}")
                        continue
                    try:
                        photo = max(post["photo"], key=lambda x: x["file_size"])
                        file_resp = self.session.get(
                            f"{self.base_url}getFile?file_id={photo['file_id']}"
                        ).json()
                        file_path = file_resp["result"]["file_path"]
                        caption = post.get("caption", "No caption")
                        results.append((
                            f"https://api.telegram.org/file/bot{self.token}/{file_path}",
                            caption,
                            unique_key,
                        ))
                        logger.info(f"Queued post {unique_key} ({len(results)}/{max_posts})")
                    except Exception as inner_e:
                        logger.error(f"Error resolving file for {unique_key}: {inner_e}")
        except Exception as e:
            logger.error(f"Error fetching telegram content: {str(e)}")
        return results

    def get_latest_image(self, channel: str, published_ids: set) -> Optional[Tuple[str, str, str]]:
        """Backward-compatible wrapper — returns only the single newest unprocessed photo post."""
        results = self.get_latest_images(channel, published_ids, max_posts=1)
        return results[0] if results else None


class VideoCreator:
    def __init__(self, config: Config):
        self.config = config
        self.music_cache = Path(".music_cache")
        self.music_cache.mkdir(exist_ok=True)
        self.fonts_dir = Path(__file__).parent / "fonts"
        self.fonts_dir.mkdir(exist_ok=True)
        # Load brand logo once
        self._logo: Optional[Image.Image] = None
        if config.LOGO_PATH and Path(config.LOGO_PATH).exists():
            try:
                logo = Image.open(config.LOGO_PATH).convert("RGBA")
                logo_size = 140  # px — fits top-right corner
                logo.thumbnail((logo_size, logo_size), Image.LANCZOS)
                self._logo = logo
                logger.info(f"Logo loaded: {logo.size}")
            except Exception as e:
                logger.warning(f"Could not load logo: {e}")

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

            # Strip emojis from text before TTS
            import re
            def strip_emojis(s: str) -> str:
                return re.sub(
                    r"[🌀-🪿"
                    r"😀-🙏"
                    r"🚀-🛿"
                    r"☀-⛿"
                    r"✀-➿"
                    r"🤀-🧿"
                    r"🇠-🇿"
                    r"‍"
                    r"︀-️]+",
                    "", s
                ).strip()

            clean_text = strip_emojis(text)

            # Append Vietnamese subscribe call-to-action
            subscribe_cta = "Don't forget to like and subscribe to the channel for more useful videos!"
            text_with_cta = f"{clean_text}. {subscribe_cta}"
            communicate = edge_tts.Communicate(text_with_cta, voice="en-SG-LunaNeural")

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


    def _generate_caption_frame(self, highlight_word: str, img: Image.Image) -> np.ndarray:
        """Show only the current spoken word centered on screen"""
        try:
            frame = img.copy().convert("RGBA")
            overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            img_w, img_h = frame.size
            font_bold = ImageFont.truetype(self.config.FONT_BOLD_PATH, 81)

            # Strip emojis from word
            clean_word = "".join(
                c for c in highlight_word
                if not (
                    0x1F300 <= ord(c) <= 0x1FABF or
                    0x1F600 <= ord(c) <= 0x1F64F or
                    0x1F680 <= ord(c) <= 0x1F6FF or
                    0x2600  <= ord(c) <= 0x26FF  or
                    0x2700  <= ord(c) <= 0x27BF  or
                    0x1F900 <= ord(c) <= 0x1F9FF or
                    0x1F1E0 <= ord(c) <= 0x1F1FF or
                    ord(c) == 0x200D            or
                    0xFE00  <= ord(c) <= 0xFE0F
                )
            ).strip(".,!?:;\"'").strip()

            if clean_word:
                padding_x = 40
                padding_y = 20

                # Measure word
                bbox = draw.textbbox((0, 0), clean_word, font=font_bold)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]

                # Center horizontally, place in lower third
                x = (img_w - text_w) // 2
                y = int(img_h * 0.75)

                # Grey semi-transparent rounded-rect background behind the word
                bg_x0 = x - padding_x
                bg_y0 = y - padding_y
                bg_x1 = x + text_w + padding_x
                bg_y1 = y + text_h + padding_y
                corner_r = 20
                draw.rounded_rectangle(
                    [bg_x0, bg_y0, bg_x1, bg_y1],
                    radius=corner_r,
                    fill=(80, 80, 80, 160)   # grey, ~63 % opaque
                )

                # Dark outline/border around text only (drawn at offsets in all directions)
                outline_color = (0, 0, 0, 255)
                outline_width = 6
                for ox in range(-outline_width, outline_width + 1):
                    for oy in range(-outline_width, outline_width + 1):
                        if ox != 0 or oy != 0:
                            draw.text((x + ox, y + oy), clean_word, font=font_bold, fill=outline_color)
                # Word in yellow on top
                draw.text((x, y), clean_word, font=font_bold, fill=(255, 220, 0, 255))

            result = Image.alpha_composite(frame, overlay)
            # Paste brand logo top-right with 20px margin
            if self._logo is not None:
                margin = 20
                lw, lh = self._logo.size
                lx = img_w - lw - margin
                ly = margin
                result.paste(self._logo, (lx, ly), self._logo)
            arr = np.array(result.convert("RGB"))
            return arr

        except Exception as e:
            logger.error(f"Caption frame failed: {str(e)}")
            return np.array(img.convert("RGB"))

    def _apply_zoom(self, img: Image.Image, progress: float) -> Image.Image:
        """Apply smooth zoom-in then zoom-out (Ken Burns effect).
        progress: 0.0 → 1.0 over the full video duration.
        Zoom peaks at the midpoint, ranging from 1.0x to 1.15x scale.
        """
        # Triangle wave: 0→1→0 mapped to zoom 1.0→1.15→1.0
        t = 1.0 - abs(progress * 2 - 1.0)   # 0..1..0
        zoom = 1.0 + 0.15 * t

        w, h = img.size
        new_w = int(w / zoom)
        new_h = int(h / zoom)

        # Crop center
        left = (w - new_w) // 2
        top  = (h - new_h) // 2
        cropped = img.crop((left, top, left + new_w, top + new_h))
        return cropped.resize((w, h), Image.LANCZOS)

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
                progress = frame_idx / max(total_frames - 1, 1)

                # Find current word being spoken
                current_word = ""
                for timing in word_timings:
                    if timing["start"] <= current_time <= timing["start"] + timing["duration"]:
                        current_word = timing["word"]
                        break

                try:
                    zoomed_img = self._apply_zoom(img, progress)
                    frame = self._generate_caption_frame(current_word, zoomed_img)
                    if frame is None:
                        raise ValueError("Frame is None")
                    frames.append(frame)
                except Exception as e:
                    logger.error(f"Frame {frame_idx} failed: {str(e)}")
                    # Fallback: use plain image without caption
                    frames.append(np.array(img.convert("RGB")))

            if not frames:
                logger.error("No frames generated")
                return None
            logger.info(f"Generated {len(frames)} frames successfully")

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
            # Merge brand hashtags (always present) + caption tags
            brand_tags = config.BRAND_HASHTAGS  # ["cryptohieuqua", "cryptohieu.com"]
            all_tags = config.TAGS + brand_tags + caption_tags

            # Build hashtag string: brand hashtags first, then top caption tags
            brand_hashtag_str = " ".join(f"#{t}" for t in brand_tags)
            caption_hashtag_str = " ".join(f"#{t}" for t in caption_tags[:5])
            hashtags = f"{brand_hashtag_str} {caption_hashtag_str}".strip()

            # Title: "Video Short + caption + datetime"
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            title = f"Video Short {caption[:50]} {date_str}"

            # Description: base description + original caption + hashtags
            caption_section = f"\n\n📌 {caption.strip()}" if caption and caption != "No caption" else ""
            description = f"{config.DESCRIPTION}{caption_section}\n\n{hashtags}\n#Shorts\n\nxeonbit24.com"

            # Schedule publish time: now + PUBLISH_DELAY_HOURS
            publish_at = (datetime.now(timezone.utc) + timedelta(hours=config.PUBLISH_DELAY_HOURS)).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
            logger.info(f"Scheduling publish at: {publish_at} UTC")

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": all_tags,
                    "categoryId": "22"
                },
                "status": {
                    "privacyStatus": "private",         # must be private for scheduled
                    "publishAt": publish_at,             # schedule publish time
                    "selfDeclaredMadeForKids": False
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

            video_id = response["id"]
            logger.info(f"Video uploaded: https://youtu.be/{video_id} (scheduled: {publish_at})")

            # Add video to target playlist
            if config.PLAYLIST_ID:
                try:
                    youtube.playlistItems().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "playlistId": config.PLAYLIST_ID,
                                "resourceId": {
                                    "kind": "youtube#video",
                                    "videoId": video_id
                                }
                            }
                        }
                    ).execute()
                    logger.info(f"Added to playlist: {config.PLAYLIST_ID}")
                except Exception as pe:
                    logger.error(f"Playlist insert failed: {pe}")

            return response
        except Exception as e:
            logger.error(f"Upload failed: {str(e)}")


async def _main():
    try:
        # Load configuration
        config = Config(
            TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"),
            TELEGRAM_CHANNELS=get_env_json("TELEGRAM_CHANNELS", '["@xeonbitchannel"]'),
            YOUTUBE_CLIENT_SECRETS=get_env_json("YOUTUBE_CLIENT_SECRETS", '{}'),
            TITLE_TEMPLATE=os.getenv("TITLE_TEMPLATE", "Video Short - {date}"),
            DESCRIPTION=os.getenv("DESCRIPTION", "Automated YouTube Short"),
            TAGS=get_env_json("TAGS", '["Shorts", "Auto-generated"]'),
            PRIVACY_STATUS=os.getenv("PRIVACY_STATUS", "private"),
            PLAYLIST_ID=os.getenv("PLAYLIST_ID", "PLKfhqWP2rL8LS6mS4eJk0sx43sD4x8TeV"),
            PUBLISH_DELAY_HOURS=int(os.getenv("PUBLISH_DELAY_HOURS", 1)),
            BRAND_HASHTAGS=get_env_json("BRAND_HASHTAGS", '["xeonbit24", "xeonbit24.com"]'),
            DURATION=int(os.getenv("DURATION", 15)),
            MUSIC_OPTION=os.getenv("MUSIC_OPTION", "music.mp3"),
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

        # Load published IDs for duplicate prevention
        published_ids_file = Path(config.PUBLISHED_IDS_FILE)
        published_ids: set = set()
        if published_ids_file.exists():
            try:
                published_ids = set(json.loads(published_ids_file.read_text()))
                logger.info(f"Loaded {len(published_ids)} published IDs")
            except Exception as e:
                logger.warning(f"Could not load published IDs: {e}")

        # Fetch up to MAX_TELEGRAM_POSTS new photos from all configured Telegram channels
        telegram = TelegramClient(config.TELEGRAM_TOKEN)
        MAX_POSTS = int(os.getenv("MAX_TELEGRAM_POSTS", 10))
        all_posts: List[Tuple[str, str, str]] = []
        for channel in config.TELEGRAM_CHANNELS:
            posts = telegram.get_latest_images(channel, published_ids, max_posts=MAX_POSTS)
            all_posts.extend(posts)
            if len(all_posts) >= MAX_POSTS:
                break

        if not all_posts:
            logger.error("No new suitable content found (all recent posts already published or no photos)")
            return

        logger.info(f"Processing {len(all_posts)} new Telegram post(s)...")
        creator = VideoCreator(config)
        uploader = YouTubeUploader(config.YOUTUBE_CLIENT_SECRETS)

        for image_url, caption, unique_key in all_posts:
            try:
                logger.info(f"Creating video for post: {unique_key}")
                video_path = await creator.create_short(image_url, caption)
                if not video_path or not video_path.exists():
                    logger.error(f"Video creation failed for {unique_key}, skipping")
                    continue

                uploader.upload_short(video_path, config, caption=caption)

                # Mark as published immediately after a successful upload
                published_ids.add(unique_key)
                try:
                    published_ids_file.write_text(json.dumps(list(published_ids)))
                    logger.info(f"Saved published ID: {unique_key}")
                except Exception as save_err:
                    logger.warning(f"Could not save published IDs: {save_err}")

                if video_path.exists():
                    video_path.unlink()
                    logger.info(f"Cleaned up temp video for {unique_key}")

            except Exception as post_err:
                logger.error(f"Error processing post {unique_key}: {post_err}")

    except Exception as e:
        logger.exception("Fatal error in main process")
        sys.exit(1)


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
