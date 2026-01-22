# bot/constants.py

# Messages
MSG_START = (
    "Ciao! Sono il bot Audio→Testo.\n"
    "Invia un messaggio vocale o un file audio, e ti restituirò il testo rielaborato.\n"
    "Usa /help per la lista comandi."
)

MSG_HELP = (
    "Ecco i comandi disponibili:\n\n"
    "/start – Messaggio di benvenuto\n"
    "/whoami – Mostra il tuo user_id e chat_id\n"
    "/adduser <id> – Aggiunge un utente alla whitelist (admin only)\n"
    "/removeuser <id> – Rimuove un utente dalla whitelist (admin only)\n"
    "/addgroup <id> – Autorizza un gruppo (admin only)\n"
    "/removegroup <id> – Rimuove un gruppo (admin only)\n"
    "/help – Mostra questo messaggio\n"
)

MSG_UNAUTHORIZED = "🚫 Non sei autorizzato a usare questo bot."
MSG_ONLY_ADMIN = "🚫 Solo admin."
MSG_USAGE_ADDUSER = "Uso: /adduser <user_id>"
MSG_USAGE_REMOVEUSER = "Uso: /removeuser <user_id>"
MSG_USAGE_ADDGROUP = "Uso: /addgroup <group_id>"
MSG_USAGE_REMOVEGROUP = "Uso: /removegroup <group_id>"
MSG_INVALID_ID = "ID non valido."
MSG_USER_ALREADY_WHITELISTED = "Utente già in whitelist."
MSG_USER_NOT_WHITELISTED = "Utente non in whitelist."
MSG_GROUP_ALREADY_AUTH = "Gruppo già autorizzato."
MSG_GROUP_NOT_AUTH = "Gruppo non autorizzato."
MSG_UNSUPPORTED_TYPE = "❌ Tipo di file non supportato."
MSG_PROCESSING = "🔄 Audio ricevuto, sto elaborando…"
MSG_ERROR_INTERNAL = "❌ Errore interno durante l'elaborazione."

# Progress messages
MSG_PROGRESS_DOWNLOAD = "⬇️ Download audio"
MSG_PROGRESS_CONVERT = "🔄 Conversione MP3"
MSG_PROGRESS_TRANSCRIBE = "🎧 Trascrizione audio"
MSG_PROGRESS_REFINE = "✍️ Rielaborazione testo"
MSG_PROGRESS_FINALIZING = "🎯 Finalizzazione"

# Timeout messages
MSG_TIMEOUT_DOWNLOAD = "⏰ Download troppo lento, riprova con file più piccoli"
MSG_TIMEOUT_CONVERT = "⏰ Conversione audio bloccata, contatta l'admin"
MSG_TIMEOUT_TRANSCRIBE = "⏰ Server LLM occupato, riprova tra pochi secondi"
MSG_TIMEOUT_REFINE = "⏰ Rielaborazione lenta, riprova più tardi"

# Error messages per fase
MSG_ERROR_DOWNLOAD = "❌ Errore nel download audio"
MSG_ERROR_CONVERT = "❌ Errore conversione MP3"
MSG_ERROR_TRANSCRIBE = "❌ Errore trascrizione audio"
MSG_ERROR_REFINE = "❌ Errore rielaborazione testo"

# Progress configuration
PROGRESS_STAGES = [
    ("⬇️ Download", "Download audio"),
    ("🔄 Conversione MP3", "Conversione in MP3"),  
    ("🎧 Trascrizione audio", "Trascrizione audio"),
    ("✍️ Rielaborazione testo", "Rielaborazione testo")
]

PROGRESS_TIMEOUTS = {
    "download": 30,      # 30 secondi max
    "convert": 60,       # 60 secondi max
    "transcribe": 120,   # 120 secondi max
    "refine": 90         # 90 secondi max
}

MSG_COMPLETION_HEADER = "📝 **Trascrizione Completata**\n🤖 Modello: {model_name}"

# Success Messages
def msg_user_added(uid): return f"✅ Utente {uid} aggiunto."
def msg_user_removed(uid): return f"✅ Utente {uid} rimosso."
def msg_group_added(gid): return f"✅ Gruppo {gid} aggiunto."
def msg_group_removed(gid): return f"✅ Gruppo {gid} rimosso."

# Default Prompts (now managed by config.py)
DEFAULT_PROMPT_SYSTEM = (
    "Sei un esperto di trascrizione audio. Correggi errori automatici, aggiungi punteggiatura, "
    "mantieni il significato originale e restituisci SOLO il testo corretto senza commenti."
)

DEFAULT_PROMPT_REFINE_TEMPLATE = (
    "Questo è un testo generato da una trascrizione automatica. Correggilo da eventuali errori, "
    "aggiungi la punteggiatura, riformula se ti rendi conto che la trascrizione è inaccurate, "
    "ma rimani il più aderente possibile al testo originale. Considera la presenza di eventuali "
    "esitazioni e ripetizioni, rendile adatte ad un testo scritto.\n"
    "IMPORTANTE: Restituisci SOLO il testo rielaborato. NON aggiungere commenti introduttivi, "
    "premese o saluti.\n\n"
    "Testo originale:\n{raw_text}\n\nTesto rielaborato:\n"
)

# Configuration
MAX_MESSAGE_LENGTH = 4000
