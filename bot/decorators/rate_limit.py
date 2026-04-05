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
        limiter = context.bot_data.get('rate_limiter')
        if limiter is None:
            raise RuntimeError("RateLimiter not initialized")
        
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
        
        admission = await limiter.request_admission(user_id, file_size_mb)

        if not admission.allowed:
            await message.reply_text(admission.message)
            return

        if admission.queued:
            await message.reply_text(admission.message)
            await limiter.wait_for_queue_turn(admission.queue_entry)
        
        try:
            # Execute the function
            return await func(update, context, *args, **kwargs)
        finally:
            # Always release the slot
            await limiter.release_async(user_id)
    
    return wrapped
