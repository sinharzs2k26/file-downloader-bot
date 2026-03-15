import os
import re
import asyncio
import logging
import math
from datetime import timedelta
import time
import tempfile
import requests
from urllib.parse import urlparse, unquote
from datetime import datetime
from typing import Optional, Tuple
from aiohttp import web
from pyrogram import Client, filters, types
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

# ===== CONFIGURATION =====
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = tempfile.mkdtemp(prefix="dl_")
UID_DIR = "./downloads/"
os.makedirs(UID_DIR, exist_ok=True)
uid_path = os.path.join(UID_DIR, "users.txt")
ADMIN_IDS = [7728700576, 7753358925]

admin_states = {}
# ===== LOGGING =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== BOT CLASS =====
class DownloadBot:
    def __init__(self):
        self.app = Client(
            "download_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        self.active_downloads = {}
        self.download_stats = {}
        self.upload_stats = {}
        self.MAX_FILE_SIZE = 2 * 1024 ** 3
        self.setup_handlers()
    
    # ===== HELPER FUNCTIONS =====
    def save_user(self, user_id):
        with open(uid_path, "a+") as f:
            f.seek(0)
            users = f.read().splitlines()
            if str(user_id) not in users:
                f.write(f"{user_id}\n")
    
    def count_users(self):
        with open(uid_path, "r") as f:
            return len(f.readlines())

    def admin_message(self):
        try:
            bot_status_txt = (f"Total users: {self.count_users()}")
        except:
            bot_status_txt = "No User ID saved in the server. Users have not sent /start yet after updating the bot."
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Broadcast", callback_data="ask_broadcast"),
             InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats")],
            [InlineKeyboardButton("🆔 Get all IDs", callback_data="get_ids")],
            [InlineKeyboardButton("📝 Update users.txt", callback_data="update_users")]
        ])
        return bot_status_txt, reply_markup

    def format_size(self, size_bytes: int) -> str:
        if size_bytes == 0:
            return "0B"
        units = ['B', 'KB', 'MB', 'GB']
        i = 0
        while size_bytes >= 1024 and i < len(units) - 1:
            size_bytes /= 1024.0
            i += 1
        return f"{size_bytes:.2f} {units[i]}"
    
    def extract_filename(self, url: str, content_type: str = None, response_headers: dict = None) -> str:
        """Extract filename from URL or response headers"""
        
        # FIRST: Try headers
        if response_headers and 'content-disposition' in response_headers:
            cd = response_headers['content-disposition']
            filename_match = re.search(r"filename\*?=([^;]+)", cd)
            if filename_match:
                filename = filename_match.group(1).strip('"').strip("'")
                if filename.startswith("UTF-8''"):
                    filename = filename.replace("UTF-8''", "")
                    filename = unquote(filename)
                
                # ===== ADD CLEANING LOGIC HERE =====
                filename = filename.split('?')[0].split('#')[0]
                filename = re.sub(r'[<>:"/\\|?*]', '', filename)
                if len(filename) > 100:
                    name, ext = os.path.splitext(filename)
                    filename = name[:95] + ext
                return filename
        
        # SECOND: Fallback to URL parsing (copy your old logic here)
        try:
            parsed = urlparse(url)
            path = unquote(parsed.path)
            filename = os.path.basename(path)
            
            if filename:
                # ===== SAME CLEANING LOGIC =====
                filename = filename.split('?')[0].split('#')[0]
                filename = re.sub(r'[<>:"/\\|?*]', '', filename)
                if len(filename) > 100:
                    name, ext = os.path.splitext(filename)
                    filename = name[:95] + ext
                
                if '.' not in filename and content_type:
                    ext = mimetypes.guess_extension(content_type)
                    if ext:
                        filename += ext
                return filename
        except:
            pass
        
        # THIRD: Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if content_type:
            ext = mimetypes.guess_extension(content_type) or '.bin'
            return f"download_{timestamp}{ext}"
        return f"download_{timestamp}.bin"
    
    def get_file_info(self, url: str) -> Tuple[Optional[int], Optional[str], Optional[dict]]:
        """Get file size, type, and headers from URL"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.head(url, headers=headers, allow_redirects=True, timeout=10)
            response.raise_for_status()
            
            size = int(response.headers.get('content-length', 0))
            content_type = response.headers.get('content-type', '')
            
            # Return headers too for filename extraction
            return size, content_type, response.headers
            
        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            return None, None, None

    def humanbytes(self, size: int) -> str:
        """Convert bytes to human readable format"""
        if not size:
            return "0 B"
        size = float(size)
        power = 1024
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        i = 0
        while size >= power and i < len(units) - 1:
            size /= power
            i += 1
        return f"{size:.2f} {units[i]}"
    # ===== COMMAND HANDLERS =====
    async def admin_command(self, client, message: Message):
        bot_status_txt, reply_markup = self.admin_message()
        await message.reply_text(bot_status_txt, reply_markup=reply_markup)

    async def start_command(self, client: Client, message: Message):
        self.save_user(message.from_user.id)
        user = message.from_user
        text = f"""
🚀 **High-Speed Download Bot (2GB Limit)**

Hey {user.first_name}! I can download files up to **2GB** using Telegram's native API!

**Features:**
• Download files up to **2GB** (50x bigger than normal bots)
• **Faster** downloads/uploads
• Shows download speed & time
• Supports all file types

**How to use:**
Just send me any direct download link!

**Commands:**
/start - This message
/help - Detailed help
/status - Bot info
"""
        await message.reply_text(text)
    
    async def help_command(self, client: Client, message: Message):
        text = f"""
📚 **Help Guide**

**What I can download:**
Any direct HTTP/HTTPS link. The URL should point directly to a file.

**Limits:**
• **2GB max** file size (Telegram native API limit)
• Direct links only (no streaming sites)

**Tips:**
• Large files take time to upload to Telegram
• Be patient with big downloads (>500MB)

**Examples:**
• https://example.com/video.mp4
• https://cdn.site/document.pdf
• https://download.site/app.zip

**Need help?** Just send a link and I'll handle it!
"""
        await message.reply_text(text)
    
    async def status_command(self, client: Client, message: Message):
        active = len(self.active_downloads)
        text = f"""
📊 **Bot Status**

• **Active downloads:** {active}
• **Max file size:** {self.format_size(self.MAX_FILE_SIZE)}
• **API:** MTProto (Native)
• **Status:** ✅ Online
• **Temp dir:** `{DOWNLOAD_DIR}`

Ready to download your files!
"""
        await message.reply_text(text)

    async def show_progress(self, current: int, total: int, message: Message, filename: str, start_time: float, action: str = "Downloading"):
        """Unified progress bar for both download and upload"""
        now = time.time()
        diff = now - start_time
        
        # Update every 2 seconds or when complete (adjust as needed)
        if round(diff % 3.00) == 0 or current == total:
            percentage = current * 100 / total
            speed = current / diff if diff > 0 else 0
            
            # Choose emoji based on action
            emoji = "⬇️" if action == "Downloading" else "📤"
            
            if speed > 0:
                time_to_completion = round((total - current) / speed)
                eta = str(timedelta(seconds=time_to_completion))
            else:
                eta = "Unknown"
            
            # Create progress bar (10 characters)
            filled = math.floor(percentage / 10)
            progress = "[{0}{1}]".format(
                ''.join(["■" for i in range(filled)]),
                ''.join(["□" for i in range(10 - filled)])
            )
            
            progress_text = (
                f"{emoji} {action}...\n"
                f"File: `{filename}`\n"
                f"<code>{progress}</code> <b>{round(percentage, 2)}%</b>\n"
                f"<b>Progress:</b> {self.humanbytes(current)} / {self.humanbytes(total)}\n"
                f"<b>Speed:</b> {self.humanbytes(speed)}/s\n"
                f"<b>ETA:</b> {eta}"
            )
            
            try:
                await message.edit_text(progress_text)
            except Exception:
                pass

    async def download_file(self, url: str, filepath: str, message: Message, filename: str, user_id: int) -> bool:
        """Download file with unified progress bar"""
        start_time = time.time()
        
        # Create initial progress message
        status_msg = await message.reply_text(
            f"⬇️ Downloading `{filename}`...\n"
            "<code>[□□□□□□□□□□]</code> <b>0%</b>\n"
            "<b>Progress:</b> 0MB / ?\n"
            "<b>Speed:</b> 0.00MB/s\n"
            "<b>ETA:</b> 0:00:00"
        )
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    # ===== CALL PROGRESS BAR HERE =====
                    if total_size > 0:
                        await self.show_progress(
                            current=downloaded,
                            total=total_size,
                            message=status_msg,
                            filename=filename,
                            start_time=start_time,
                            action="Downloading"  # This shows ⬇️
                        )

                    # Yield control to event loop
                    await asyncio.sleep(0)

        # Final progress update at 100%
        await self.show_progress(
            current=downloaded,
            total=total_size,
            message=status_msg,
            filename=filename,
            start_time=start_time,
            action="Downloading"
        )
        
        # Store stats for later
        end_time = time.time()
        download_time = end_time - start_time
        if download_time > 0 and downloaded > 0:
            avg_speed = downloaded / download_time
            self.download_stats[user_id] = {
                'time': download_time,
                'speed': self.humanbytes(avg_speed) + "/s",
                'size': downloaded
            }
        
        await asyncio.sleep(1)  # Brief pause to see 100%
        await status_msg.delete()
        return True
    
    async def handle_msg(self, client: Client, message: Message):
        if admin_states["admin_action"] == "waiting_for_msg":
            admin_states["admin_action"] = None
            if not os.path.exists(uid_path):
                await message.reply_text("No users to broadcast to.")
                return
            with open(uid_path, "r") as f:
                users = f.read().splitlines()
        
            status = await message.reply_text(f"🚀 Sending to {len(users)} users...")
            success = 0
            failed = 0
            for uid in users:
                try:
                    await message.copy(chat_id=int(uid))
                    success += 1
                    await asyncio.sleep(0.05)
                except:
                    failed += 1
            user_s = "users" if success > 1 else "user"
            fail_msg = f"\n❌ Failed {failed}" if failed > 1 else ""
            await status.edit_text(f"✅ **Broadcast Done**\nSent to {success} {user_s}.{fail_msg}")
        else:
            user_id = message.from_user.id
            url = message.text.strip()
            
            # Check URL format
            if not (url.startswith('http://') or url.startswith('https://')):
                await message.reply_text("❌ Please send a valid HTTP/HTTPS link")
                return
            
            # Check if already downloading
            if user_id in self.active_downloads:
                await message.reply_text("⏳ You have a download in progress. Please wait...")
                return
            
            # Get file info
            status_msg = await message.reply_text("🔍 Analyzing URL...")
            file_size, content_type, headers = self.get_file_info(url)
            
            if file_size is None:
                await status_msg.edit_text("❌ Cannot access file. Invalid URL or server blocked.")
                return
            
            # Check size limit (2GB)
            if file_size > self.MAX_FILE_SIZE:
                await status_msg.edit_text(
                    f"❌ File too large: {self.format_size(file_size)}\n"
                    f"Limit: {self.format_size(self.MAX_FILE_SIZE)}"
                )
                return
            
            # Get filename
            filename = self.extract_filename(url, content_type, headers)
            size_readable = self.format_size(file_size)
            
            # Confirm download
            await status_msg.edit_text(
                f"📄 **File Info**\n"
                f"Name: `{filename}`\n"
                f"Size: {size_readable}\n\n"
            )
            
            # Download file
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            self.active_downloads[user_id] = filename
            
            success = await self.download_file(url, filepath, message, filename, user_id)
            
            if not success:
                if user_id in self.active_downloads:
                    del self.active_downloads[user_id]
                return
    
            upload_start = time.time()
            upload_msg = await message.reply_text("📤 Preparing to upload...")
            
            try:
                # Get file stats
                file_stat = os.stat(filepath)
                file_size = file_stat.st_size
                
                # Track uploaded bytes manually if needed, or use callback
                uploaded_bytes = 0
                
                # Define a wrapper callback that tracks total uploaded
                async def upload_callback(current, total, *args):
                    nonlocal uploaded_bytes
                    uploaded_bytes = current
                    await self.show_progress(
                        current=current,
                        total=total,
                        message=upload_msg,
                        filename=filename,
                        start_time=upload_start,
                        action="Uploading"
                    )
                
                # Determine file type and send with progress callback
                ext = os.path.splitext(filename)[1].lower()
                
                if ext in ['.mp4', '.avi', '.mkv', '.mov', '.webm']:
                    await message.reply_video(
                        video=filepath,
                        caption=f"🎬 `{filename}`",
                        progress=upload_callback,  # Use wrapper callback
                        progress_args=(file_size,)  # Pyrogram passes current,total
                    )
                elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                    await message.reply_photo(
                        photo=filepath,
                        caption=f"🖼️ `{filename}`",
                        progress=upload_callback,
                        progress_args=(file_size,)
                    )
                elif ext in ['.mp3', '.wav', '.ogg', '.flac']:
                    await message.reply_audio(
                        audio=filepath,
                        caption=f"🎵 `{filename}`",
                        progress=upload_callback,
                        progress_args=(file_size,)
                    )
                else:
                    await message.reply_document(
                        document=filepath,
                        caption=f"📁 `{filename}`",
                        progress=upload_callback,
                        progress_args=(file_size,)
                    )
                
                upload_time = time.time() - upload_start
                
                # Calculate upload speed
                if upload_time > 0 and file_size > 0:
                    upload_speed = file_size / upload_time
                    upload_speed_str = self.humanbytes(upload_speed) + "/s"
                else:
                    upload_speed_str = "N/A"
                
                # Store upload stats
                self.upload_stats[user_id] = {
                    'time': upload_time,
                    'speed': upload_speed_str,
                    'size': file_size
                }
                
                # Get download stats
                download_stats = self.download_stats.get(user_id, {})
                download_time_str = f"{download_stats.get('time', 0):.1f}s" if download_stats else "N/A"
                download_speed_str = download_stats.get('speed', 'N/A')
                
                # Show final completion message with BOTH speeds
                await upload_msg.edit_text(
                    f"✅ **Complete!**\n"
                    f"📥 Download: {download_time_str} - {download_speed_str}\n"
                    f"📤 Upload: {upload_time:.1f}s - {upload_speed_str}\n"
                )
                
            except Exception as e:
                logger.error(f"Upload error: {e}")
                await upload_msg.edit_text(f"❌ Upload failed: {str(e)[:100]}")
            
            # Cleanup
            if user_id in self.active_downloads:
                del self.active_downloads[user_id]
            if user_id in self.download_stats:
                del self.download_stats[user_id]
            if os.path.exists(filepath):
                os.remove(filepath)
    
    def setup_handlers(self):
        @self.app.on_message(filters.command("admin") & filters.user(ADMIN_IDS))
        async def admin_handler(client, message):
            await self.admin_command(client, message)
        
        @self.app.on_message(filters.command("start"))
        async def start_handler(client, message):
            await self.start_command(client, message)
        
        @self.app.on_message(filters.command("help"))
        async def help_handler(client, message):
            await self.help_command(client, message)
        
        @self.app.on_message(filters.command("status"))
        async def status_handler(client, message):
            await self.status_command(client, message)

        @self.app.on_message(filters.text)
        async def msg_handler(client, message):
            await self.handle_msg(client, message)
        
        @self.app.on_callback_query()
        async def handle_buttons(client, cb):
            bot_status_txt, reply_markup = self.admin_message()
            if cb.data == "refresh_stats":
                try:
                    await cb.edit_message_text(bot_status_txt, reply_markup=reply_markup)
                    await cb.answer("Refreshed!")
                except:
                    await cb.answer("No updates yet!")
            elif cb.data == "get_ids":
                await cb.answer()
                if os.path.exists(uid_path):
                    await cb.message.reply_document(uid_path, caption="Here is the current user list.")
                else:
                    await cb.answer("File not found!", show_alert=True)
            elif cb.data == "update_users":
                admin_states["admin_action"] = "waiting_for_file"
                await cb.edit_message_text(
                    "📤 **Update User List**\n\nPlease send me a new `.txt` file containing the User IDs (one per line).",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
                )
            elif cb.data == "ask_broadcast":
                await cb.answer()
                admin_states["admin_action"] = "waiting_for_msg"
                await cb.edit_message_text(
                    "📝 **Broadcast Mode**\n\nSend the message you want to broadcast now.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
                )
            elif cb.data == "cancel":
                await cb.answer()
                admin_states["admin_action"] = None
                await cb.edit_message_text(bot_status_txt, reply_markup=reply_markup)

        @self.app.on_message(filters.private)
        async def admin_input_handler(client, message):
            state = admin_states.get("admin_action")
            if not state:
                return
            if state == "waiting_for_file":
                if message.document and message.document.file_name.endswith(".txt"):
                    await message.download(file_name=uid_path)
                    admin_states["admin_action"] = None
                    await message.reply_text("✅ `users.txt` has been updated successfully!")
                else:
                    await message.reply_text("❌ Please send a valid `.txt` file.")
                return

    async def health_check(self, request):
        return web.Response(text="Bot is running!", status=200)
    
    async def run_http_server(self):
        app = web.Application()
        app.router.add_get('/', self.health_check)
        app.router.add_get('/health', self.health_check)    
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 10000)
        await site.start()

    def run(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self.run_http_server())
        self.app.run()

# ===== MAIN =====
if __name__ == "__main__":
    bot = DownloadBot()
    bot.run()