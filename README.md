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

## 🔧 Troubleshooting

- **`FFmpeg is not installed`**: Ensure FFmpeg is installed and accessible via command line (`ffmpeg -version`).
- **`TELEGRAM_TOKEN is required`**: Verify your `.env` file exists and is correctly formatted.
- **409 Conflict**: The bot is already running elsewhere. Stop other instances.
- **Transcription hangs**: Check your API quota (OpenAI/Gemini).

## 📦 Project Structure

- `bot/main.py`: Entry point and bootstrapper.
- `bot/handlers/`: Telegram command logic (Audio, Admin, Base).
- `bot/core/`: Application setup.
- `bot/providers.py`: LLM provider implementations.
- `audio_files/`: Temporary storage (auto-cleaned).

## 📝 Changelog

See [CHANGELOG.md](./CHANGELOG.md) for version history.

## 📄 License

This project is licensed under the MIT License – see the [LICENSE](./LICENSE) file for details.