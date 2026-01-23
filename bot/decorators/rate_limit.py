from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

def rate_limited(func):
    """
    Decorator to enforce rate limits on audio processing.
    
    Limits per user:
    - Max concurrent requests: 2
    - Global limit: 6
    - File size limit: 20MB
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # Get rate limiter instance
        from bot.handlers.audio import get_rate_limiter
        limiter = get_rate_limiter()
        
        # Get user info
        user_id = update.effective_user.id if update.effective_user else 0
        
        # Get file size estimate
        message = update.message
        file_size_mb = 0
        if message:
            if message.voice:
                file_size_mb = (message.voice.file_size or 0) / (1024 * 1024)
            elif message.audio:
                file_size_mb = (message.audio.file_size or 0) / (1024 * 1024)
            elif message.document:
                file_size_mb = (message.document.file_size or 0) / (1024 * 1024)
        
        # Check limits
        allowed, msg = await limiter.check_limit(user_id, file_size_mb)
        
        if not allowed:
            await message.reply_text(msg)
            return
        
        try:
            # Execute the function
            return await func(update, context, *args, **kwargs)
        finally:
            # Always release the slot
            await limiter.release_async(user_id)
    
    return wrapped