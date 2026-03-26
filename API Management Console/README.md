# Local AI Chatbot Deployment Console

A powerful, local-first AI deployment platform for creating, managing, and deploying multiple chatbots. This system runs entirely on your desktop and provides a secure web-based management UI at localhost.

![Version](https://img.shields.io/badge/version-1.1.0-blue)
![Python](https://img.shields.io/badge/python-3.10+-green)
![License](https://img.shields.io/badge/license-MIT-orange)

## 🆕 What's New (v1.1.0)

### Chat Preview Stats Panel
- **Real-time statistics** - View message count, token usage, and response times
- **Activity log** - Track sent messages and received responses
- **Status indicator** - See bot status (Ready/Thinking/Error)
- **Model info** - Display provider, model name, and temperature

### Improved Startup Process
- **One-click launch** - Run `start_console.bat` for automated setup
- **Auto browser opening** - Dashboard opens automatically after server starts
- **Smart model detection** - Counts and displays installed Ollama models
- **Comprehensive logging** - All startup events logged to `logs/` folder

### Knowledge Base Management
- **Visual knowledge viewer** - Browse all knowledge chunks in bot editor
- **Search and filter** - Find specific content in the knowledge base
- **Pagination** - Handle large knowledge bases efficiently
- **One-click delete** - Remove individual chunks

### Enhanced Reliability
- **Setup script improvements** - `setup_ollama.bat` checks existing models before downloading
- **Error handling** - Better diagnostics and error messages
- **Application logging** - Detailed logs in `logs/app_YYYYMMDD.log`

## 🎯 Features

### Multi-Chatbot System
- Create and manage unlimited chatbots
- Unique configuration per bot (system prompt, temperature, model)
- Per-bot knowledge bases with RAG (Retrieval Augmented Generation)
- Conversation memory toggle per bot
- Message limits for demo mode
- Expiration dates for time-limited deployments

### Provider Support
- **OpenAI** - GPT-4.1, GPT-4o, GPT-3.5-turbo, and more
- **Local LLM** - llama.cpp, Ollama, LM Studio, vLLM
- **Claude** - Anthropic models (future-ready)

### Knowledge Base Management
- Upload TXT, JSON, Markdown files
- Automatic text chunking and embedding
- FAISS vector search for relevant context
- Rebuild embeddings on demand

### Website Scraper
- Scrape websites to create knowledge bases
- Configurable crawl depth (1-3 levels)
- Respects robots.txt
- Rate-limited requests
- Review extracted data before creating bot

### Admin Dashboard
- Full-featured web UI at `http://localhost:8000/admin`
- Bot list with status, provider, messages
- Edit bot configurations
- View analytics and message logs
- Clone existing bots

### Embeddable Chat Widget
- Clean, mobile-responsive chat UI
- **Stats panel** with real-time metrics (tokens, latency, message count)
- **Activity log** showing message history
- Customizable accent color
- Works in iframes
- Ready-to-use embed snippets (HTML, React, HubSpot)

### Analytics & Logging
- Per-message logging with tokens and latency
- Daily analytics aggregation
- Usage trends visualization
- Token cost estimation
- **Application logs** in `logs/` folder
- **Startup diagnostics** for troubleshooting

### Security
- Encrypted API key storage
- Admin authentication with JWT
- Rate limiting per IP and per bot
- CORS protection


## 🚀 Quick Start

### Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- (Optional) Ollama for local LLM support

### One-Click Start (Recommended)

**Windows users can use the automated launcher:**

1. **Double-click** `start_console.bat` or run from terminal:
   ```batch
   .\start_console.bat
   ```

The script will automatically:
- ✅ Check Python installation
- ✅ Create/activate virtual environment  
- ✅ Install dependencies (first run only)
- ✅ Load configuration from `.env`
- ✅ Start Ollama if available
- ✅ Start the web server
- ✅ Open admin dashboard in browser

### Manual Installation

1. **Clone or download the project:**
   ```bash
   cd "API Management Console"
   ```

2. **Create virtual environment:**
   ```bash
   python -m venv venv
   
   # Windows
   .\venv\Scripts\activate
   
   # Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment:**
   ```bash
   # Copy example config
   copy .env.example .env
   
   # Edit .env with your settings
   # Add your OpenAI API key, etc.
   ```

5. **Start the server:**
   ```bash
   python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

6. **Access the dashboard:**
   - Admin UI: http://localhost:8000/admin
   - API Docs: http://localhost:8000/docs
   - Default login: `admin` / `admin123`

### Quick Ollama Setup

For local LLM support, run the Ollama setup script:
```batch
.\setup_ollama.bat
```

This installs recommended lightweight models (~8GB total):
- qwen2.5:1.5b (default)
- llama3.2:1b, llama3.2:3b
- phi3:mini
- gemma2:2b


## 📁 Project Structure

```
API Management Console/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database.py         # SQLAlchemy models
│   │   └── schemas.py          # Pydantic schemas
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── admin.py            # Admin dashboard API
│   │   ├── auth.py             # Authentication
│   │   ├── bots.py             # Bot management API
│   │   ├── chat.py             # Public chat API
│   │   ├── embed.py            # Embeddable widget
│   │   ├── scraper.py          # Web scraper API
│   │   └── templates.py        # Template management
│   └── services/
│       ├── __init__.py
│       ├── analytics_service.py
│       ├── bot_service.py
│       ├── embedding_service.py
│       ├── encryption_service.py
│       ├── scraper_service.py
│       ├── template_service.py
│       └── llm_providers/
│           ├── __init__.py
│           ├── base_provider.py
│           ├── claude_provider.py
│           ├── local_provider.py
│           └── openai_provider.py
├── data/
│   ├── chatbot_console.db      # SQLite database
│   └── bots/
│       └── {bot_id}/
│           ├── documents/
│           └── embeddings/
├── logs/                        # Application logs
│   ├── app_YYYYMMDD.log        # Daily app logs
│   └── startup_*.log           # Startup diagnostic logs
├── templates/
│   ├── openai_template.json
│   ├── local_template.json
│   └── claude_template.json
├── start_console.bat            # One-click launcher (Windows)
├── setup_ollama.bat             # Ollama model setup script
├── .env
├── .env.example
├── requirements.txt
└── README.md
```


## 🔧 Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ADMIN_USERNAME` | Admin login username | `admin` |
| `ADMIN_PASSWORD` | Admin login password | `admin123` |
| `JWT_SECRET` | Secret for JWT tokens | Random |
| `ENCRYPTION_SECRET` | Secret for API key encryption | Random |
| `OPENAI_API_KEY` | Your OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Your Anthropic API key | - |
| `LOCAL_LLM_URL` | Local LLM server URL | `http://localhost:8080` |
| `RATE_LIMIT_REQUESTS` | Requests per window | `60` |
| `RATE_LIMIT_WINDOW` | Rate limit window (seconds) | `60` |
| `CORS_ORIGINS` | Allowed CORS origins | `*` |


## 📡 API Reference

### Public Endpoints (No Auth Required)

#### Chat with Bot
```http
POST /api/bot/{bot_id}/chat
Content-Type: application/json

{
  "message": "Hello!",
  "session_id": "optional-session-uuid"
}
```

#### Get Bot Status
```http
GET /api/bot/{bot_id}/status
```

### Admin Endpoints (Auth Required)

#### Login
```http
POST /api/admin/login
Content-Type: application/json

{
  "username": "admin",
  "password": "admin123"
}
```

#### List Bots
```http
GET /api/bots
Authorization: Bearer {token}
```

#### Create Bot
```http
POST /api/bots
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "My Assistant",
  "template_type": "openai_template",
  "provider_type": "openai",
  "api_key": "sk-..."
}
```

#### Upload Knowledge
```http
POST /api/bots/{bot_id}/knowledge
Authorization: Bearer {token}
Content-Type: multipart/form-data

content: "Your knowledge content here..."
source: "manual_upload"
append: false
```

Full API documentation available at `/docs` when server is running.


## 🌐 Embedding the Chat Widget

### Simple iframe

```html
<iframe 
  src="http://localhost:8000/embed/{bot_id}" 
  width="100%" 
  height="600" 
  frameborder="0">
</iframe>
```

### Floating Widget (JavaScript)

```html
<script>
(function() {
    var iframe = document.createElement('iframe');
    iframe.src = 'http://localhost:8000/embed/{bot_id}';
    iframe.style.cssText = 'position:fixed;bottom:20px;right:20px;width:380px;height:520px;border:none;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.2);z-index:9999;';
    document.body.appendChild(iframe);
})();
</script>
```

### HubSpot Module

```html
<!-- Add to HubSpot custom module -->
<div id="chatbot-container" style="width: 100%; height: 600px;">
    <iframe 
        src="http://your-domain.com/embed/{bot_id}" 
        width="100%" 
        height="100%" 
        frameborder="0">
    </iframe>
</div>
```


## 🌍 Exposing Publicly

### Using ngrok

```bash
# Install ngrok
choco install ngrok  # Windows
brew install ngrok   # Mac

# Start tunnel
ngrok http 8000
```

### Using Cloudflare Tunnel

```bash
# Install cloudflared
# https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/

# Create tunnel
cloudflared tunnel create ai-console
cloudflared tunnel route dns ai-console your-subdomain.yourdomain.com

# Run tunnel
cloudflared tunnel run ai-console
```

### Production Deployment

For production, consider:

1. **Change default credentials** in `.env`
2. **Use HTTPS** (via reverse proxy like nginx)
3. **Set specific CORS origins**
4. **Use a proper secret** for `JWT_SECRET` and `ENCRYPTION_SECRET`
5. **Set up backups** for the SQLite database


## 🤖 Using Local LLMs

The console supports local language models via **Ollama** (recommended) or other OpenAI-compatible servers. When using local LLMs, the console will automatically:
- Start Ollama if it's not running
- Verify your selected model is installed
- Provide helpful error messages if setup is needed

### Recommended Models for Consumer Hardware

| Model | Disk Size | Best For |
|-------|-----------|----------|
| `qwen2.5:1.5b` | ~1 GB | **Default** - General tasks, fast responses |
| `llama3.2:1b` | ~1.3 GB | Ultra lightweight, basic Q&A |
| `llama3.2:3b` | ~2 GB | Good balance of speed/quality |
| `phi3:mini` | ~2.2 GB | Strong reasoning, compact |
| `gemma2:2b` | ~1.6 GB | Efficient, good multilingual |

### Quick Setup with Ollama

**Option 1: Automated Setup (Recommended)**
```batch
# Run the setup script to install all recommended models
.\setup_ollama.bat
```

**Option 2: Manual Setup**
```bash
# 1. Install Ollama from https://ollama.ai

# 2. Start Ollama (if not already running)
ollama serve

# 3. Install your preferred models
ollama pull qwen2.5:1.5b    # Recommended default
ollama pull llama3.2:1b     # Lightest option
ollama pull llama3.2:3b     # Better quality
ollama pull phi3:mini       # Best reasoning
ollama pull gemma2:2b       # Google's model

# 4. List installed models
ollama list
```

### Creating a Local LLM Bot

1. Open Admin Dashboard: `http://localhost:8000/admin`
2. Click "Create Bot"
3. Select **"Local LLM"** as the provider
4. Choose a model from the dropdown (only installed models shown)
5. Configure system prompt and other settings
6. Click "Create"

### Troubleshooting Local LLMs

**"Ollama is not running" error:**
```bash
# Start Ollama manually
ollama serve
```
*Note: The console will attempt to start Ollama automatically.*

**"Model not found" error:**
```bash
# Install the missing model
ollama pull <model_name>

# Example:
ollama pull qwen2.5:1.5b
```

**Check installed models:**
```bash
ollama list
```

### With llama.cpp Server

```bash
# Start llama.cpp server (update LOCAL_LLM_URL=http://localhost:8080 in .env)
./server -m your-model.gguf -c 4096 --host 0.0.0.0 --port 8080
```

### With LM Studio

1. Download and install LM Studio
2. Load a model
3. Start local server (Settings > Server)
4. Update `LOCAL_LLM_URL` in `.env` to match the server address


## 📊 Analytics

The dashboard provides:

- **Total messages** per bot and overall
- **Token usage** with cost estimates
- **Average latency** for performance monitoring
- **Daily breakdown** for trend analysis
- **Top questions** to understand user needs


## 🛠 Development

### Running Tests

```bash
pytest tests/ -v
```

### Adding a New Provider

1. Create `app/services/llm_providers/new_provider.py`
2. Extend `BaseLLMProvider`
3. Implement required methods: `chat`, `chat_stream`, `get_embeddings`
4. Register in `app/services/llm_providers/__init__.py`
5. Create template in `templates/new_provider_template.json`


## 📝 License

MIT License - Feel free to use, modify, and distribute.


## 🤝 Contributing

Contributions welcome! Please read the contributing guidelines first.


## ⚠️ Important Notes

1. **API Keys**: Never commit `.env` files with real API keys
2. **Security**: Change default admin credentials in production
3. **Rate Limiting**: Adjust rate limits based on your needs
4. **Backups**: Regularly backup `data/chatbot_console.db`
5. **Scraping**: Always obtain consent before scraping websites


## 📞 Support

For issues and feature requests, please open a GitHub issue.

---

Built with ❤️ using FastAPI, SQLite, FAISS, and OpenAI
