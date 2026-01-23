## 👋 Preface

I’m a technology enthusiast and I work as a **Sales Engineer** in a tech company, where part of my role is specifically focused on **Generative AI**. I’m not a software developer (I only have a basic understanding of the fundamentals), but out of personal interest and continuous learning I decided to experiment with tools such as **Antigravity** and **OpenCode**.

For this reason, the entire repository has been developed using what is often called **“vibecoding”**, with the sole purpose of testing **agentic tools designed for software development**. The chosen use case is intentionally simple and well within the reach of many human developers, precisely because the real goal is to observe how these tools reason, navigate, and interpret a small but real codebase.

The decision to publish this repository is driven by two main reasons:

- **Sharing the experience** by including in the repository also the files used by OpenCode (such as `AGENTS.md`), in order to better understand how these agentic systems analyze and interpret a small codebase like this one.
- **Quite simply, because the bot works**: I use it daily together with a few friends, and it has proven to be genuinely useful and reliable.

# Telegram Audio Transcriber Bot 🎙️🤖

An advanced Telegram bot that transcribes voice notes and audio files, processes the text to improve readability, and automatically manages length limits and file cleanup.

## ✨ Features

- **Multi-Provider LLM**: Native support for **OpenAI** (Whisper + GPT) and **Google Gemini** (multimodal).
- **Audio Transcription**: Supports Telegram voice notes and audio files (mp3, ogg, wav, etc.) via FFmpeg.
- **Smart Refinement**: Corrects errors, adds punctuation, and formats transcribed text using configurable LLMs.
- **Long Message Handling**: Automatically splits responses that exceed Telegram's 4096-character limit.
- **Access Control**: Integrated whitelist to authorize individual users (admin/user) or specific groups.
- **Rate Limiting**: Configurable per-user and global limits to prevent abuse and manage server load.
- **Auto Cleanup**: Temporary audio files are deleted immediately after processing to save disk space.
- **Configurable Prompts**: Customize bot behavior without touching the code.

## 🚀 Getting Started

### Prerequisites
- **FFmpeg**: Must be installed and available on your system's PATH.
- **Telegram Bot Token**: Get one from [@BotFather](https://t.me/BotFather).
- **API Key**: Depending on your provider preference (OpenAI or Google Gemini).

### 🐳 Docker (Recommended)

The easiest way to run the bot is using Docker Compose.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/NdR91/ndr-telegram-audio-bot.git
   cd ndr-telegram-audio-bot
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your TELEGRAM_TOKEN and API keys
   ```

3. **Configure permissions**:
   Create an `authorized.json` file (see [Configuration](#-configuration) below).

4. **Start the bot**:
   ```bash
   docker-compose up -d --build
   ```
   View logs with `docker-compose logs -f`.

### 🛠️ Manual Installation (Local)

1. **Install FFmpeg**:
   - Ubuntu: `sudo apt-get install ffmpeg`
   - macOS: `brew install ffmpeg`

2. **Set up Python environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configuration**:
   Copy `.env.example` to `.env` and configure your keys. Create `authorized.json`.

4. **Run**:
   ```bash
   python bot/main.py
   ```

## ⚙️ Configuration

### Environment Variables (`.env`)

See `.env.example` for all available options.

**Basic Setup:**
```bash
TELEGRAM_TOKEN=your_token_here
```

**Provider Selection:**
- **OpenAI** (Default): Uses Whisper for audio and GPT-4o-mini for refinement.
  ```bash
  LLM_PROVIDER=openai
  OPENAI_API_KEY=sk-...
  ```
- **Google Gemini**: Uses native multimodal audio processing.
  ```bash
  LLM_PROVIDER=gemini
  GEMINI_API_KEY=AIza...
  LLM_MODEL=gemini-2.0-flash  # Optional: override model
  ```

**Rate Limiting (Optional):**
Customize request limits to manage server load and prevent abuse.
  ```bash
  RATE_LIMIT_PER_USER=2          # Max concurrent requests per user
  RATE_LIMIT_COOLDOWN=30         # Cooldown in seconds after hitting limit
  RATE_LIMIT_GLOBAL=6            # Max global concurrent requests
  RATE_LIMIT_FILE_SIZE=20        # Max file size in MB (Telegram limit is 20MB)
  ```

### Access Control (`authorized.json`)

Create a file named `authorized.json` in the root directory. This controls who can use the bot.

**Note**: To find your ID, start the bot and run `/whoami`.

```json
{
  "admin": [123456789],
  "users": [],
  "groups": [-100123456789]
}
```

- **admin**: Full access + management commands.
- **users**: Can use transcription features.
- **groups**: All members of the group can use the bot.

## 🎮 Commands

### User Commands
- `/start` - Welcome message.
- `/whoami` - Display your User ID and current Chat ID.
- `/help` - Show available commands.

### Admin Commands
- `/adduser <id>` - Add a user to the whitelist.
- `/removeuser <id>` - Remove a user.
- `/addgroup <id>` - Authorize a group.
- `/removegroup <id>` - Remove a group.

**Note**: Rate limiting configuration is managed via `.env` file.

## 🔧 Troubleshooting

- **`FFmpeg is not installed`**: Ensure FFmpeg is installed and accessible via command line (`ffmpeg -version`).
- **`TELEGRAM_TOKEN is required`**: Verify your `.env` file exists and is correctly formatted.
- **409 Conflict**: The bot is already running elsewhere. Stop other instances.
- **Transcription hangs**: Check your API quota (OpenAI/Gemini).
- **`File troppo grande`**: File exceeds the configured limit (default 20MB). Send a smaller file.
- **`Il bot è occupato`**: Global rate limit reached. Wait a moment and try again.
- **`Attendi ancora Xs`**: Per-user rate limit reached. Wait for cooldown to expire.

## 📦 Project Structure

```text
.
├── audio_files/          # Temporary storage (auto-cleaned)
├── bot/
│   ├── core/             # Application builder & setup
│   ├── decorators/       # Authentication, timeouts & rate limiting
│   ├── handlers/         # Telegram commands logic (audio, admin, commands)
│   ├── ui/               # Progress bars & feedback
│   ├── config.py         # Centralized configuration
│   ├── constants.py      # Messages & prompts
│   ├── main.py           # Entry point
│   ├── providers.py      # LLM interfaces (OpenAI/Gemini)
│   ├── rate_limiter.py   # Rate limiting system
│   └── utils.py          # FFmpeg & helpers
├── .env.example          # Environment variables template
├── authorized.json       # Access control list (not committed)
├── docker-compose.yml    # Docker configuration
└── requirements.txt      # Python dependencies
```

## 📝 Changelog

See [CHANGELOG.md](./CHANGELOG.md) for version history.

## 🗺️ Roadmap

- [x] Concurrent processing (v20260123.1)
- [x] Rate limiting system (v20260123)
- [x] Enhanced error handling (v20260123)
- [x] Centralized configuration (v20260122)
- [ ] Request queue after 6 concurrent limit
- [ ] Multilingual support (UI + auto-detect)
- [ ] Health checks endpoint (monitoring)
- [ ] Circuit breaker (API failure recovery)

## 📄 License

This project is licensed under the MIT License – see the [LICENSE](./LICENSE) file for details.