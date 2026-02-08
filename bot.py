import os
import re
import logging
import requests
import asyncio
import mimetypes
import tempfile
import time
from urllib.parse import urlparse, unquote
from typing import Optional, Tuple, Dict
from datetime import datetime
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from telegram.constants import ParseMode
from telegram.error import BadRequest

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration - Get from Environment Variables (for Render.com)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
PORT = int(os.environ.get('PORT', 10000))  # Render.com provides PORT
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit
ALLOWED_EXTENSIONS = {
    '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm',
    '.mp3', '.wav', '.ogg', '.m4a', '.flac',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt',
    '.zip', '.rar', '.7z', '.tar', '.gz',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.apk', '.exe', '.dmg', '.iso'
}

class TelegramDownloadBot:
    def __init__(self):
        self.active_downloads = {}
        self.download_stats = {}
        self.temp_dir = tempfile.mkdtemp(prefix="tg_downloads_")
        logger.info(f"Created temp directory: {self.temp_dir}")
        
        # Ensure temp directory exists
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir, exist_ok=True)
    
    # ===== Helper Functions =====
    
    def clean_filename(self, filename: str) -> str:
        """Clean filename by removing invalid characters"""
        # Remove query strings and fragments
        filename = filename.split('?')[0].split('#')[0]
        # Remove invalid characters
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        # Replace spaces with underscores
        filename = filename.replace(' ', '_')
        # Limit length
        if len(filename) > 100:
            name, ext = os.path.splitext(filename)
            filename = name[:95] + ext
        return filename
    
    def extract_filename_from_url(self, url: str, content_type: str = None) -> str:
        """Extract filename from URL"""
        try:
            parsed = urlparse(url)
            path = unquote(parsed.path)
            filename = os.path.basename(path)
            
            if filename:
                filename = self.clean_filename(filename)
                # Ensure it has an extension
                if '.' not in filename and content_type:
                    ext = mimetypes.guess_extension(content_type)
                    if ext:
                        filename += ext
                return filename
            
            # If no filename in URL, generate one
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if content_type:
                ext = mimetypes.guess_extension(content_type) or '.bin'
                return f"download_{timestamp}{ext}"
            return f"download_{timestamp}.bin"
            
        except Exception as e:
            logger.error(f"Error extracting filename: {e}")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return f"download_{timestamp}.bin"
    
    def is_valid_url(self, url: str) -> bool:
        """Validate URL format"""
        pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
            r'localhost|'  # localhost
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
            r'(?::\d+)?'  # port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
        return bool(pattern.match(url))
    
    def get_file_info(self, url: str) -> Tuple[Optional[int], Optional[str]]:
        """Get file size and type from URL headers"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.head(url, headers=headers, allow_redirects=True, timeout=10)
            response.raise_for_status()
            
            size = int(response.headers.get('content-length', 0))
            content_type = response.headers.get('content-type', '')
            
            # If HEAD doesn't give size, try GET with range
            if size == 0:
                response = requests.get(url, headers=headers, stream=True, timeout=5)
                size = int(response.headers.get('content-length', 0))
                content_type = response.headers.get('content-type', content_type)
            
            return size, content_type
        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            return None, None
    
    def format_size(self, size_bytes: int) -> str:
        """Convert bytes to human readable format"""
        if size_bytes == 0:
            return "0B"
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        i = 0
        while size_bytes >= 1024 and i < len(units) - 1:
            size_bytes /= 1024.0
            i += 1
        return f"{size_bytes:.2f} {units[i]}"
    
    def is_extension_allowed(self, filename: str) -> bool:
        """Check if file extension is allowed"""
        _, ext = os.path.splitext(filename.lower())
        return ext in ALLOWED_EXTENSIONS or ext == ''  # Allow files without extension
    
    # ===== Bot Command Handlers =====
    
    async def start_command(self, update: Update, context: CallbackContext):
        """Handle /start command"""
        user = update.effective_user
        welcome_text = f"""
🤖 Welcome {user.first_name}!

I'm your personal download assistant. I can download files from direct links and send them to you.

How to use:
1. Send me any direct download link (HTTP/HTTPS)
2. I'll download it and send it back to you

Supported files:
• Videos (MP4, AVI, MKV, etc.)
• Documents (PDF, DOC, XLS, PPT, etc.)
• Archives (ZIP, RAR, 7Z, etc.)
• Images (JPG, PNG, GIF, etc.)
• Audio (MP3, WAV, etc.)
• Apps (APK, EXE, DMG, etc.)

Limits:
• Max file size: {self.format_size(MAX_FILE_SIZE)} (Telegram limit)
• Direct links only (no streaming sites)

Commands:
/start - Show this message
/help - Detailed help
/status - Bot status

Just send me a link to get started!
        """
        await update.message.reply_text(welcome_text)
    
    async def help_command(self, update: Update, context: CallbackContext):
        """Handle /help command"""
        help_text = f"""
📚 Help Guide

What I can download:
Any file accessible via a direct HTTP/HTTPS link. The link should end with a filename like:
• https://example.com/files/video.mp4
• https://cdn.example.com/document.pdf
• https://download.example.com/app.zip

How to use:
1. Copy a direct download link
2. Paste it here
3. I'll handle the rest!

File size limits:
• Maximum: {self.format_size(MAX_FILE_SIZE)} (Telegram Bot API limit)
• Larger files will be rejected automatically

Troubleshooting:
❌ "Invalid URL" - Make sure it starts with http:// or https://
❌ "File too large" - File exceeds {self.format_size(MAX_FILE_SIZE)}
❌ "Download failed" - Server might be blocking bots or link is broken
❌ "Unsupported file" - File type not in allowed list

Need help?
Just send me a link and I'll try to download it!
        """
        
        await update.message.reply_text(help_text)

    async def status_command(self, update: Update, context: CallbackContext):
        """Handle /status command"""
        active_count = len(self.active_downloads)
        bot_uptime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        status_text = f"""
📊 Bot Status
• Active downloads: {active_count}
• Server time: {bot_uptime}
• Max file size: {self.format_size(MAX_FILE_SIZE)}
• Ready: ✅

Storage:
• Temp directory: {self.temp_dir}
• Files will be automatically cleaned up
        """
        await update.message.reply_text(status_text)
    
    async def handle_url_message(self, update: Update, context: CallbackContext):
        """Handle URL messages"""
        user_id = update.effective_user.id
        url = update.message.text.strip()
        
        # Check if already downloading
        if user_id in self.active_downloads:
            await update.message.reply_text("⏳ You already have a download in progress. "
                                          "Wait")
            return
        
        # Validate URL
        if not self.is_valid_url(url):
            await update.message.reply_text("❌ Invalid URL\n"
                                          "Please send a valid HTTP/HTTPS link starting with http:// or https://")
            return
        
        # Send initial status
        status_msg = await update.message.reply_text("🔍 Analyzing URL...")
        
        try:
            # Get file info
            file_size, content_type = self.get_file_info(url)
            
            if file_size is None:
                await status_msg.edit_text("❌ Cannot access file\n"
                                         "The server might be blocking bots or the link is invalid.")
                return
            
            # Check file size
            if file_size > MAX_FILE_SIZE:
                size_readable = self.format_size(file_size)
                max_readable = self.format_size(MAX_FILE_SIZE)
                await status_msg.edit_text(f"❌ File too large\n"
                                         f"Size: {size_readable}\n"
                                         f"Limit: {max_readable}\n"
                                         f"\nPlease use a smaller file.")
                return
            
            # Extract filename
            filename = self.extract_filename_from_url(url, content_type)
            
            # Check file extension
            if not self.is_extension_allowed(filename):
                await status_msg.edit_text(f"⚠️ Unsupported file type\n"
                                         f"File: {filename}\n"
                                         f"\nI support common file types only.")
                return
            
            # Show file info
            size_readable = self.format_size(file_size)
            file_type = content_type.split(';')[0] if content_type else 'Unknown'
            
            await status_msg.edit_text(f"📄 File Info\n"
                                     f"Name: {filename}\n"
                                     f"Size: {size_readable}\n"
                                     f"Type: {file_type}\n"
                                     f"\n Download in progress... ⏳")
            
            # Start download
            self.active_downloads[user_id] = filename
            
            filepath = os.path.join(self.temp_dir, filename)

            # Download with progress
            success = await self.download_file(url, filepath, status_msg, user_id, filename)
            
            if not success:
                if user_id in self.active_downloads:
                    del self.active_downloads[user_id]
                return
                
            # Send file to user
            await self.send_file_to_user(update, filepath, filename, status_msg)
            
            # Clean up
            if user_id in self.active_downloads:
                del self.active_downloads[user_id]
            if os.path.exists(filepath):
                os.remove(filepath)
            
        except Exception as e:
            logger.error(f"Error in handle_url_message: {e}")
            await status_msg.edit_text(f"❌ Error\n"
                                     f"\n{str(e)[:200]}\n\n"
                                     f"\nPlease try again or use a different link.")
            user_id = update.effective_user.id
            if user_id in self.active_downloads:
                filename = self.active_downloads[user_id]
                del self.active_downloads[user_id]
    
    async def download_file(self, url: str, filepath: str, status_msg, user_id: int, filename: str) -> bool:
        """Download file with measuring speed"""
        try:
            start_time = time.time()
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
            # Download with measuring speed
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            end_time = time.time()
            download_time = end_time - start_time
            
            # Calculate average speed
            file_size = os.path.getsize(filepath)
            if download_time > 0:
                avg_speed = file_size / download_time  # bytes per second
                avg_speed_str = self.format_size(avg_speed) + "/s"
            else:
                avg_speed_str = "N/A"
            
            # Store for final message
            self.download_stats[user_id] = {
                'download_time': download_time,
                'avg_speed': avg_speed_str,
                'file_size': file_size
            }
            
            return True
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            await status_msg.edit_text(f"❌ Download Failed\nError: {str(e)[:100]}")
            return False
    
    async def send_file_to_user(self, update: Update, filepath: str, filename: str, status_msg):
        """Send downloaded file to user with download stats"""
        try:
            user_id = update.effective_user.id
            file_size = os.path.getsize(filepath)
            
            # Get download stats if available
            stats = self.download_stats.get(user_id, {})
            download_time = stats.get('download_time', 0)
            avg_speed = stats.get('avg_speed', 'N/A')
            
            # Format download time
            if download_time < 60:
                time_str = f"{download_time:.1f} seconds"
            elif download_time < 3600:
                time_str = f"{download_time/60:.1f} minutes"
            else:
                time_str = f"{download_time/3600:.1f} hours"
            
            # Show completion message with stats
            await status_msg.edit_text(
                f"✅ Download Complete!\n"
                f"File: {filename}\n"
                f"Size: {self.format_size(file_size)}\n"
                f"Time: {time_str}\n"
                f"Avg Speed: {avg_speed}\n"
                f"\n📤 Uploading to Telegram..."
            )
            
            # Remove stats after showing
            if user_id in self.download_stats:
                del self.download_stats[user_id]
        
            # Determine file type and send appropriately
            mime_type, _ = mimetypes.guess_type(filepath)
            
            with open(filepath, 'rb') as file:
                if mime_type and mime_type.startswith('video/'):
                    await update.message.reply_video(
                        video=InputFile(file, filename=filename),
                        caption=f"🎬 {filename}",
                        supports_streaming=True
                    )
                elif mime_type and mime_type.startswith('image/'):
                    await update.message.reply_photo(
                        photo=InputFile(file, filename=filename),
                        caption=f"🖼️ {filename}"
                    )
                elif mime_type and mime_type.startswith('audio/'):
                    await update.message.reply_audio(
                        audio=InputFile(file, filename=filename),
                        caption=f"🎵 {filename}"
                    )
                else:
                    await update.message.reply_document(
                        document=InputFile(file, filename=filename),
                        caption=f"📁 {filename}"
                    )
            
            await status_msg.delete()
            
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            # Try to send as document if specific type fails
            try:
                with open(filepath, 'rb') as file:
                    await update.message.reply_document(
                        document=InputFile(file, filename=filename),
                        caption=f"📁 {filename}",
                    )
                await status_msg.delete()
            except Exception as e2:
                await status_msg.edit_text(f"❌ Upload Failed\n"
                                         f"Error: {str(e2)[:100]}\n"
                                         f"\nFile might be too large or format not supported.")
    
    async def cleanup_temp_files(self):
        """Clean up temporary files periodically"""
        try:
            for filename in os.listdir(self.temp_dir):
                filepath = os.path.join(self.temp_dir, filename)
                try:
                    # Remove files older than 1 hour
                    if os.path.isfile(filepath):
                        file_age = datetime.now().timestamp() - os.path.getmtime(filepath)
                        if file_age > 3600:  # 1 hour
                            os.remove(filepath)
                            logger.info(f"Cleaned up old file: {filename}")
                except Exception as e:
                    logger.error(f"Error cleaning up {filename}: {e}")
        except Exception as e:
            logger.error(f"Error in cleanup: {e}")
    
    # ===== Bot Setup and Run =====
    
    def setup_handlers(self, application: Application):
        """Set up bot command handlers"""
        # Command handlers
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("status", self.status_command))
        
        # URL handler
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_url_message
        ))
        
        # Error handler
        async def error_handler(update: Update, context: CallbackContext):
            logger.error(f"Update {update} caused error {context.error}")
            
        application.add_error_handler(error_handler)
    
    def run_polling(self):
        """Run bot with polling (for local testing)"""
        application = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers(application)
        logger.info("Starting bot in polling mode...")
        application.run_polling()

# ===== Main Execution =====

def main():
    """Main function to run the bot"""
    
    # Check for bot token
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN environment variable is not set!")
        print("\nTo set it up:")
        print("1. Create a bot on Telegram with @BotFather")
        print("2. Copy the bot token")
        print("3. Set it as environment variable:")
        print("   - On Render.com: Add to Environment Variables")
        print("   - Locally: export BOT_TOKEN='your_token_here'")
        print("\nOr edit the BOT_TOKEN variable in the code (not recommended for production)")
        return
    
    print(f"""
╔═══════════════════════════════════════╗
║     Telegram Download Manager Bot     ║
╚═══════════════════════════════════════╝
    
📊 Config:
• Max file size: {MAX_FILE_SIZE / (1024*1024):.0f}MB
• Temp directory: {tempfile.gettempdir()}
• Port: {PORT}
    
Starting bot...
    """)
    # Create health server
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Bot is alive!')

        def log_message(self, format, *args):
            pass  # Silence logs

    def run_health_server():
        port = PORT
        httpd = HTTPServer(('0.0.0.0', port), HealthHandler)
        logger.info(f"✅ Health server on port {port}")
        httpd.serve_forever()

    # Start health server
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Create and run bot
    bot = TelegramDownloadBot()
    bot.run_polling()

if __name__ == "__main__":
    main()