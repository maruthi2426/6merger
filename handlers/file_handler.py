"""Handle file uploads including rclone config file detection."""
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.file_manager import FileManager
from utils.ffmpeg_processor import FFmpegProcessor
from handlers.media_processor import (
    process_extract, process_trim, 
    process_convert, process_compress, process_remove_stream,
    process_swap_audio, process_combine, process_watermark, 
    process_subtitle
)
from handlers.video_merge_processor import process_merge_video

logger = logging.getLogger(__name__)
file_manager = FileManager()
processor = FFmpegProcessor()

async def download_file_with_fallback(context: ContextTypes.DEFAULT_TYPE, file, filepath: str, user_id: int, update: Update = None) -> bool:
    """
    Download file with intelligent fallback to Pyrogram for large files.
    
    Bot API getFile has 50MB hard limit enforced by Telegram servers.
    For files > 50MB, automatically switch to Pyrogram MTProto protocol.
    
    Args:
        context: Telegram context
        file: File object from update.message
        filepath: Local path to save file
        user_id: User ID
        update: Update object (required for large files to get chat_id/message_id)
    
    Returns:
        True if download successful, False otherwise
    """
    try:
        BOT_API_LIMIT = 50 * 1024 * 1024  # 50MB hard limit
        file_size = getattr(file, "file_size", 0)
        
        # If file is within Bot API limit, use it (faster)
        if file_size > 0 and file_size <= BOT_API_LIMIT:
            logger.info(f"File size: {file_size / (1024*1024):.2f}MB - Using Bot API")
            file_obj = await context.bot.get_file(file.file_id)
            await file_obj.download_to_drive(filepath)
            logger.info(f"Downloaded file using Bot API: {filepath}")
            return True
        
        # If file exceeds Bot API limit, skip directly to Pyrogram
        if file_size > BOT_API_LIMIT:
            logger.warning(f"File size: {file_size / (1024*1024):.2f}MB exceeds 50MB Bot API limit - Using Pyrogram MTProto")
            
            if not update:
                logger.error("Update object required for large file download")
                return False
            
            try:
                from handlers.pyrogram_setup import get_or_create_pyrogram_client
                
                # Get Pyrogram client for large file download
                pyrogram_client = await get_or_create_pyrogram_client(str(user_id))
                if not pyrogram_client:
                    raise Exception("Failed to initialize Pyrogram client")
                
                # Pyrogram MTProto needs message_id and chat_id, not bot file_id
                await pyrogram_client.start()
                
                chat_id = update.effective_chat.id
                message_id = update.message.message_id
                
                # Download using Pyrogram MTProto (supports up to 2GB)
                msg = await pyrogram_client.get_messages(chat_id, message_id)
                
                if msg and msg.document or msg.video or msg.audio:
                    downloaded_path = await msg.download(file_name=filepath)
                    
                    if downloaded_path and os.path.exists(filepath):
                        await pyrogram_client.stop()
                        logger.info(f"Downloaded large file using Pyrogram: {filepath}")
                        return True
                
                await pyrogram_client.stop()
                raise Exception("Pyrogram download returned no file")
            
            except Exception as pyrogram_error:
                logger.error(f"Pyrogram fallback failed: {pyrogram_error}")
                return False
        
        # If file size is 0 (unknown), try Bot API with exception handling
        logger.info(f"File size unknown (0 bytes reported) - attempting Bot API")
        file_obj = await context.bot.get_file(file.file_id)
        await file_obj.download_to_drive(filepath)
        logger.info(f"Downloaded file using Bot API: {filepath}")
        return True
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Download failed: {error_msg}")
        
        # If Bot API failed with size error, try Pyrogram as last resort
        if ("too big" in error_msg.lower() or "413" in error_msg or "400" in error_msg) and update:
            logger.warning(f"Bot API failed with error: {error_msg} - attempting Pyrogram fallback")
            try:
                from handlers.pyrogram_setup import get_or_create_pyrogram_client
                
                pyrogram_client = await get_or_create_pyrogram_client(str(user_id))
                await pyrogram_client.start()
                
                chat_id = update.effective_chat.id
                message_id = update.message.message_id
                
                msg = await pyrogram_client.get_messages(chat_id, message_id)
                if msg:
                    downloaded_path = await msg.download(file_name=filepath)
                    await pyrogram_client.stop()
                    
                    if downloaded_path and os.path.exists(filepath):
                        logger.info(f"Fallback: Downloaded large file using Pyrogram: {filepath}")
                        return True
                
                await pyrogram_client.stop()
            except Exception as fallback_error:
                logger.error(f"Fallback also failed: {fallback_error}")
        
        return False

async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all file uploads and process based on operation."""
    try:
        user_id = update.effective_user.id
        
        if context.user_data.get("awaiting_rclone_config"):
            file = update.message.document
            if not file:
                await update.message.reply_text(
                    "❌ Please send a file (document format only)",
                    reply_to_message_id=update.message.message_id
                )
                return
            
            filename = file.file_name or "rclone.conf"
            
            # Only accept .conf files
            if not filename.endswith(".conf"):
                await update.message.reply_text(
                    "❌ Invalid file! Please send the rclone.conf file only",
                    reply_to_message_id=update.message.message_id
                )
                return
            
            # Create userdata directory
            user_dir = f"./userdata/{user_id}"
            os.makedirs(user_dir, exist_ok=True)
            
            # Save the config file
            conf_path = os.path.join(user_dir, "rclone.conf")
            
            success = await download_file_with_fallback(context, file, conf_path, user_id, update)
            if not success:
                await update.message.reply_text(
                    "❌ Failed to download rclone.conf file",
                    reply_to_message_id=update.message.message_id
                )
                return
            
            # Validate rclone config
            try:
                with open(conf_path, 'r') as f:
                    content = f.read()
                    if '[' not in content or ']' not in content:
                        raise ValueError("Invalid rclone config format")
                
                context.user_data["upload_mode"] = {
                    "engine": "rclone",
                    "configured": True
                }
                context.user_data.pop("awaiting_rclone_config", None)
                
                logger.info(f"User {user_id} successfully uploaded rclone.conf")
                
                try:
                    await update.message.reply_to_message.delete()
                except Exception as e:
                    logger.warning(f"Could not delete rclone setup message: {e}")
                
                try:
                    await update.message.delete()
                except Exception as e:
                    logger.warning(f"Could not delete rclone file message: {e}")
                
                from keyboards.main_keyboard import get_main_keyboard
                
                await update.message.reply_text(
                    text="✅ RCLONE CONFIGURED\n━━━━━━━━━━━━━━━━━━\n\n"
                         "📋 rclone.conf: Successfully added\n\n"
                         "🎬 Welcome to Video Merger Bot!\n\nSelect a category:",
                    reply_markup=get_main_keyboard(context.user_data.get("upload_mode"))
                )
                return
            except Exception as e:
                logger.error(f"Invalid rclone config from user {user_id}: {e}")
                await update.message.reply_text(
                    f"❌ Invalid rclone config file!\n\n"
                    f"Error: {str(e)}\n\n"
                    f"Please send a valid rclone.conf file",
                    reply_to_message_id=update.message.message_id
                )
                os.remove(conf_path)
                return
        
        if context.user_data.get("awaiting_merge_filename"):
            filename_text = update.message.text
            
            if not filename_text:
                await update.message.reply_text(
                    "❌ Please send a valid filename",
                    reply_to_message_id=update.message.message_id
                )
                return
            
            # Validate and normalize filename
            filename_text = filename_text.strip()
            
            # Ensure .mp4 extension
            if not filename_text.lower().endswith('.mp4'):
                filename_text = filename_text + '.mp4'
            
            # Remove invalid characters
            invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
            for char in invalid_chars:
                filename_text = filename_text.replace(char, '')
            
            context.user_data["merged_filename"] = filename_text
            context.user_data.pop("awaiting_merge_filename", None)
            
            try:
                await update.message.reply_to_message.delete()
            except Exception as e:
                logger.warning(f"Could not delete asking message: {e}")
            
            try:
                await update.message.delete()
            except Exception as e:
                logger.warning(f"Could not delete filename message: {e}")
            
            # Show continue button as fresh message
            await update.message.reply_text(
                text=f"✅ FILENAME SET\n━━━━━━━━━━━━━━━━━━\n\n"
                     f"📁 Filename: {filename_text}\n\n"
                     f"Ready to merge!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("▶️ Continue", callback_data="merge_filename_continue"),
                    InlineKeyboardButton("❌ Cancel", callback_data="merge_menu")
                ]])
            )
            return
        
        operation = context.user_data.get("operation")
        
        if not operation:
            await update.message.reply_text(
                "❌ No operation selected. Use /start to choose an operation",
                reply_to_message_id=update.message.message_id
            )
            return
        
        file = update.message.document or update.message.video or update.message.audio
        if not file:
            return
        
        filename = file.file_name or f"file_{file.file_id[:8]}"
        filepath = os.path.join(file_manager.TEMP_FOLDER, filename)
        
        # Create temp folder
        file_manager.create_temp_folder()
        
        if operation in ["merge", "merge_add"]:
            download_msg = await update.message.reply_text(
                "📥 Downloading video...\n"
                "Progress: 0%",
                reply_to_message_id=update.message.message_id
            )
            
            success = await download_file_with_fallback(context, file, filepath, user_id, update)
            try:
                await download_msg.delete()
            except:
                pass
            
            if not success:
                await update.message.reply_text(
                    "❌ Failed to download video file. Please try again.",
                    reply_to_message_id=update.message.message_id
                )
                return
            
            await process_merge_video(update, context, filepath)
        else:
            success = await download_file_with_fallback(context, file, filepath, user_id, update)
            if not success:
                await update.message.reply_text(
                    "❌ Failed to download file. Please try again.",
                    reply_to_message_id=update.message.message_id
                )
                return
            
            file_size = file_manager.get_file_size(filepath) / (1024*1024)
            await update.message.reply_text(
                f"📥 Downloaded: {filename} ({file_size:.2f} MB)",
                reply_to_message_id=update.message.message_id
            )
            
            if "files" not in context.user_data:
                context.user_data["files"] = []
            context.user_data["files"].append(filepath)
            
            if operation == "extract":
                await process_extract(update, context, filepath)
            elif operation == "trim":
                await process_trim(update, context, filepath)
            elif operation == "convert":
                await process_convert(update, context, filepath)
            elif operation == "compress":
                await process_compress(update, context, filepath)
            elif operation == "remove_stream":
                await process_remove_stream(update, context, filepath)
            elif operation == "swap_audio":
                await process_swap_audio(update, context, filepath)
            elif operation == "combine":
                await process_combine(update, context, filepath)
            elif operation == "watermark":
                await process_watermark(update, context, filepath)
            elif operation == "subtitle":
                await process_subtitle(update, context, filepath)
        
    except Exception as e:
        logger.error(f"Error handling file: {e}")
        await update.message.reply_text(
            f"❌ Error: {str(e)}",
            reply_to_message_id=update.message.message_id
        )
