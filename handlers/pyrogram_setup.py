"""Initialize Pyrogram client for large file uploads via MTProto."""
import logging
import os
from pyrogram import Client

logger = logging.getLogger(__name__)

# Cache for pyrogram clients per user
pyrogram_clients = {}

async def get_or_create_pyrogram_client(user_id: str) -> Client:
    """
    Get or create a Pyrogram client for MTProto uploads.
    Each user gets their own session file.
    
    Requires environment variables:
    - TELEGRAM_API_ID: Your Telegram API ID (from https://my.telegram.org)
    - TELEGRAM_API_HASH: Your Telegram API Hash (from https://my.telegram.org)
    """
    if user_id in pyrogram_clients:
        return pyrogram_clients[user_id]
    
    try:
        api_id = int(os.getenv("TELEGRAM_API_ID", "31315704"))
        api_hash = os.getenv("TELEGRAM_API_HASH", "e9a0fcbaf23eb7d872732e87cbb012cc")
        
        if not api_id or not api_hash:
            logger.error("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH in environment")
            return None
        
        # Create user session directory
        session_dir = f"./userdata/{user_id}"
        os.makedirs(session_dir, exist_ok=True)
        
        # Create client with user session (in-memory for no file persistence)
        client = Client(
            name=f"user_{user_id}",
            api_id=api_id,
            api_hash=api_hash,
            workdir=session_dir,
            no_updates=True  # Disable update handling for upload-only client
        )
        
        pyrogram_clients[user_id] = client
        logger.info(f"Created Pyrogram MTProto client for user {user_id}")
        return client
    
    except Exception as e:
        logger.error(f"Error creating Pyrogram client: {e}")
        return None


async def initialize_pyrogram_for_upload(context, user_id: str) -> bool:
    """
    Initialize Pyrogram client for large file uploads.
    Stores client in context for use during merge.
    """
    try:
        client = await get_or_create_pyrogram_client(user_id)
        if client:
            context.user_data["pyrogram_client"] = client
            logger.info(f"Pyrogram MTProto client ready for user {user_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error initializing Pyrogram: {e}")
        return False
