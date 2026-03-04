"""
Embeddable chat widget routes.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..models.database import get_db, Bot
from ..models.schemas import EmbedConfigResponse
from ..services.bot_service import bot_service

router = APIRouter(prefix="/embed", tags=["embed"])


def get_embed_html(bot: Bot) -> str:
    """Generate embeddable chat widget HTML."""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{bot.name}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #f5f5f5;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        
        .chat-wrapper {{
            display: flex;
            height: 100vh;
            width: 100%;
        }}
        
        .stats-panel {{
            width: 280px;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: white;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            overflow-y: auto;
            font-size: 13px;
        }}
        
        .stats-panel h3 {{
            font-size: 14px;
            color: #a0aec0;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 4px;
        }}
        
        .stat-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 8px;
            padding: 12px;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        
        .stat-label {{
            font-size: 11px;
            color: #718096;
            margin-bottom: 4px;
        }}
        
        .stat-value {{
            font-size: 18px;
            font-weight: 600;
            color: #63b3ed;
        }}
        
        .stat-value.success {{ color: #48bb78; }}
        .stat-value.warning {{ color: #ed8936; }}
        
        .stat-row {{
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        
        .stat-row:last-child {{
            border-bottom: none;
        }}
        
        .thinking-indicator {{
            display: none;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            background: rgba(99, 179, 237, 0.2);
            border-radius: 6px;
            color: #63b3ed;
            font-size: 12px;
        }}
        
        .thinking-indicator.active {{
            display: flex;
        }}
        
        .pulse {{
            width: 8px;
            height: 8px;
            background: #63b3ed;
            border-radius: 50%;
            animation: pulse 1s infinite;
        }}
        
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: 0.5; transform: scale(0.8); }}
        }}
        
        .log-list {{
            max-height: 200px;
            overflow-y: auto;
            font-size: 11px;
        }}
        
        .log-entry {{
            padding: 4px 8px;
            margin: 2px 0;
            background: rgba(255,255,255,0.03);
            border-radius: 4px;
            color: #a0aec0;
        }}
        
        .log-entry.sent {{ border-left: 2px solid #63b3ed; }}
        .log-entry.received {{ border-left: 2px solid #48bb78; }}
        .log-entry.error {{ border-left: 2px solid #fc8181; color: #fc8181; }}
        
        .chat-container {{
            flex: 1;
            display: flex;
            flex-direction: column;
            background: white;
        }}
        
        .chat-header {{
            background: {bot.accent_color};
            color: white;
            padding: 16px;
            text-align: center;
            font-weight: 600;
            font-size: 16px;
        }}
        
        .chat-messages {{
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        
        .message {{
            max-width: 85%;
            padding: 12px 16px;
            border-radius: 16px;
            line-height: 1.5;
            font-size: 14px;
        }}
        
        .message.user {{
            background: {bot.accent_color};
            color: white;
            align-self: flex-end;
            border-bottom-right-radius: 4px;
        }}
        
        .message.assistant {{
            background: #f0f0f0;
            color: #333;
            align-self: flex-start;
            border-bottom-left-radius: 4px;
        }}
        
        .message.system {{
            background: #fff3cd;
            color: #856404;
            align-self: center;
            text-align: center;
            font-size: 13px;
        }}
        
        .chat-input {{
            display: flex;
            padding: 12px;
            background: white;
            border-top: 1px solid #e0e0e0;
            gap: 8px;
        }}
        
        .chat-input input {{
            flex: 1;
            padding: 12px 16px;
            border: 1px solid #ddd;
            border-radius: 24px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s;
        }}
        
        .chat-input input:focus {{
            border-color: {bot.accent_color};
        }}
        
        .chat-input button {{
            background: {bot.accent_color};
            color: white;
            border: none;
            border-radius: 50%;
            width: 44px;
            height: 44px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: opacity 0.2s;
        }}
        
        .chat-input button:hover {{
            opacity: 0.9;
        }}
        
        .chat-input button:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
        
        .typing-indicator {{
            display: none;
            align-self: flex-start;
            padding: 12px 16px;
            background: #f0f0f0;
            border-radius: 16px;
            border-bottom-left-radius: 4px;
        }}
        
        .typing-indicator.show {{
            display: flex;
            gap: 4px;
        }}
        
        .typing-indicator span {{
            width: 8px;
            height: 8px;
            background: #999;
            border-radius: 50%;
            animation: typing 1.4s infinite ease-in-out;
        }}
        
        .typing-indicator span:nth-child(1) {{ animation-delay: 0s; }}
        .typing-indicator span:nth-child(2) {{ animation-delay: 0.2s; }}
        .typing-indicator span:nth-child(3) {{ animation-delay: 0.4s; }}
        
        @keyframes typing {{
            0%, 60%, 100% {{ transform: translateY(0); }}
            30% {{ transform: translateY(-6px); }}
        }}
        
        .powered-by {{
            text-align: center;
            padding: 8px;
            font-size: 11px;
            color: #999;
            background: #fafafa;
        }}
        
        @media (max-width: 600px) {{
            .stats-panel {{
                display: none;
            }}
        }}
    </style>
</head>
<body>
    <div class="chat-wrapper">
        <!-- Stats Panel -->
        <div class="stats-panel">
            <div>
                <h3>Bot Info</h3>
                <div class="stat-card">
                    <div class="stat-row">
                        <span class="stat-label">Model</span>
                        <span style="color: #63b3ed;">{bot.model or 'Default'}</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Provider</span>
                        <span style="color: #a0aec0;">{bot.provider_type}</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Temperature</span>
                        <span style="color: #a0aec0;">{bot.temperature}</span>
                    </div>
                </div>
            </div>
            
            <div>
                <h3>Session Stats</h3>
                <div class="stat-card">
                    <div class="stat-row">
                        <span class="stat-label">Messages Sent</span>
                        <span id="stat-messages" class="stat-value">0</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Total Tokens</span>
                        <span id="stat-tokens">0</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Avg Response</span>
                        <span id="stat-avg-time">0ms</span>
                    </div>
                </div>
            </div>
            
            <div>
                <h3>Current Status</h3>
                <div id="thinking-indicator" class="thinking-indicator">
                    <div class="pulse"></div>
                    <span>Processing...</span>
                </div>
                <div class="stat-card">
                    <div class="stat-row">
                        <span class="stat-label">Status</span>
                        <span id="stat-status" class="stat-value success">Ready</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Last Response</span>
                        <span id="stat-last-time">-</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Last Tokens</span>
                        <span id="stat-last-tokens">-</span>
                    </div>
                </div>
            </div>
            
            <div>
                <h3>Activity Log</h3>
                <div class="stat-card">
                    <div id="log-list" class="log-list">
                        <div class="log-entry">Session started</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Chat Container -->
        <div class="chat-container">
            <div class="chat-header">{bot.name}</div>
            
            <div class="chat-messages" id="messages">
                <div class="message assistant">{bot.welcome_message}</div>
            </div>
            
            <div class="typing-indicator" id="typing">
                <span></span>
                <span></span>
                <span></span>
            </div>
            
            <div class="chat-input">
                <input type="text" id="input" placeholder="Type your message..." autocomplete="off">
                <button id="send" onclick="sendMessage()">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
                    </svg>
                </button>
            </div>
            
            <div class="powered-by">Powered by Local AI Console</div>
        </div>
    </div>

    <script>
        const BOT_ID = '{bot.id}';
        const API_BASE = window.location.origin;
        let sessionId = localStorage.getItem('session_' + BOT_ID) || generateUUID();
        localStorage.setItem('session_' + BOT_ID, sessionId);
        
        // Stats tracking
        let stats = {{
            messageCount: 0,
            totalTokens: 0,
            responseTimes: []
        }};
        
        function generateUUID() {{
            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {{
                var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            }});
        }}
        
        const messagesEl = document.getElementById('messages');
        const inputEl = document.getElementById('input');
        const sendBtn = document.getElementById('send');
        const typingEl = document.getElementById('typing');
        const thinkingEl = document.getElementById('thinking-indicator');
        
        function updateStats(tokens, responseTime) {{
            stats.messageCount++;
            stats.totalTokens += tokens || 0;
            if (responseTime) stats.responseTimes.push(responseTime);
            
            document.getElementById('stat-messages').textContent = stats.messageCount;
            document.getElementById('stat-tokens').textContent = stats.totalTokens.toLocaleString();
            
            if (stats.responseTimes.length > 0) {{
                const avg = Math.round(stats.responseTimes.reduce((a,b) => a+b) / stats.responseTimes.length);
                document.getElementById('stat-avg-time').textContent = avg + 'ms';
            }}
        }}
        
        function updateCurrentStats(status, responseTime, tokens) {{
            const statusEl = document.getElementById('stat-status');
            statusEl.textContent = status;
            statusEl.className = 'stat-value ' + (status === 'Ready' ? 'success' : status === 'Error' ? 'warning' : '');
            
            if (responseTime) {{
                document.getElementById('stat-last-time').textContent = responseTime + 'ms';
            }}
            if (tokens) {{
                document.getElementById('stat-last-tokens').textContent = tokens.toLocaleString();
            }}
        }}
        
        function addLog(message, type) {{
            const logList = document.getElementById('log-list');
            const entry = document.createElement('div');
            entry.className = 'log-entry ' + (type || '');
            const time = new Date().toLocaleTimeString();
            entry.textContent = time + ' - ' + message;
            logList.appendChild(entry);
            logList.scrollTop = logList.scrollHeight;
            
            // Keep only last 50 entries
            while (logList.children.length > 50) {{
                logList.removeChild(logList.firstChild);
            }}
        }}
        
        inputEl.addEventListener('keypress', (e) => {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }});
        
        function addMessage(text, type) {{
            const msg = document.createElement('div');
            msg.className = 'message ' + type;
            msg.textContent = text;
            messagesEl.appendChild(msg);
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }}
        
        async function sendMessage() {{
            const text = inputEl.value.trim();
            if (!text) return;
            
            // Disable input
            inputEl.value = '';
            inputEl.disabled = true;
            sendBtn.disabled = true;
            
            // Add user message
            addMessage(text, 'user');
            addLog('Sent: ' + text.substring(0, 30) + (text.length > 30 ? '...' : ''), 'sent');
            
            // Show typing indicator
            typingEl.classList.add('show');
            thinkingEl.classList.add('active');
            updateCurrentStats('Thinking...', null, null);
            
            const startTime = Date.now();
            
            try {{
                const response = await fetch(API_BASE + '/bot/' + BOT_ID + '/chat', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{
                        message: text,
                        session_id: sessionId
                    }})
                }});
                
                const responseTime = Date.now() - startTime;
                const data = await response.json();
                
                // Hide typing indicator
                typingEl.classList.remove('show');
                thinkingEl.classList.remove('active');
                
                if (response.ok) {{
                    addMessage(data.response, 'assistant');
                    sessionId = data.session_id;
                    localStorage.setItem('session_' + BOT_ID, sessionId);
                    
                    // Update stats
                    updateStats(data.tokens_used, responseTime);
                    updateCurrentStats('Ready', responseTime, data.tokens_used);
                    addLog('Response received (' + responseTime + 'ms, ' + (data.tokens_used || 0) + ' tokens)', 'received');
                }} else {{
                    addMessage(data.detail || 'An error occurred', 'system');
                    updateCurrentStats('Error', null, null);
                    addLog('Error: ' + (data.detail || 'Unknown error'), 'error');
                }}
            }} catch (error) {{
                typingEl.classList.remove('show');
                thinkingEl.classList.remove('active');
                addMessage('Connection error. Please try again.', 'system');
                updateCurrentStats('Error', null, null);
                addLog('Connection error', 'error');
            }}
            
            // Re-enable input
            inputEl.disabled = false;
            sendBtn.disabled = false;
            inputEl.focus();
        }}
    </script>
</body>
</html>'''


@router.get("/{bot_id}", response_class=HTMLResponse)
async def get_embed_widget(
    bot_id: str,
    db: Session = Depends(get_db)
):
    """
    Get embeddable chat widget for a bot.
    
    This route serves a self-contained HTML page that can be embedded in an iframe.
    """
    bot = bot_service.get_bot(db, bot_id)
    if not bot:
        return HTMLResponse(
            content="<html><body><h1>Chatbot not found</h1></body></html>",
            status_code=404
        )
    
    if not bot.is_available():
        return HTMLResponse(
            content=f'''<html><body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
                <div style="text-align: center; color: #666;">
                    <h2>Chatbot Unavailable</h2>
                    <p>This chatbot is currently inactive.</p>
                </div>
            </body></html>''',
            status_code=200
        )
    
    return HTMLResponse(content=get_embed_html(bot))


@router.get("/{bot_id}/config", response_model=EmbedConfigResponse)
async def get_embed_config(
    bot_id: str,
    db: Session = Depends(get_db)
):
    """Get embed configuration for a bot."""
    bot = bot_service.get_bot(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )
    
    return EmbedConfigResponse(
        bot_id=bot.id,
        bot_name=bot.name,
        accent_color=bot.accent_color,
        welcome_message=bot.welcome_message,
        is_available=bot.is_available()
    )


@router.get("/{bot_id}/snippet")
async def get_embed_snippet(
    bot_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Get embed code snippets for different platforms.
    """
    bot = bot_service.get_bot(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )
    
    base_url = str(request.base_url).rstrip('/')
    embed_url = f"{base_url}/embed/{bot_id}"
    
    return {
        "bot_id": bot_id,
        "bot_name": bot.name,
        "embed_url": embed_url,
        "snippets": {
            "iframe": f'''<iframe 
    src="{embed_url}" 
    width="100%" 
    height="600" 
    frameborder="0"
    allow="clipboard-write"
    title="{bot.name}">
</iframe>''',
            
            "hubspot": f'''<!-- HubSpot Custom Module HTML -->
<div id="chatbot-container" style="width: 100%; height: 600px;">
    <iframe 
        src="{embed_url}" 
        width="100%" 
        height="100%" 
        frameborder="0"
        allow="clipboard-write"
        title="{bot.name}">
    </iframe>
</div>''',
            
            "javascript": f'''<!-- Floating Chat Widget -->
<script>
(function() {{
    var chatWidget = document.createElement('div');
    chatWidget.id = 'ai-chat-widget';
    chatWidget.innerHTML = `
        <div id="chat-toggle" style="
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: {bot.accent_color};
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9998;
        ">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="white">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
        </div>
        <div id="chat-frame" style="
            display: none;
            position: fixed;
            bottom: 90px;
            right: 20px;
            width: 380px;
            height: 520px;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 24px rgba(0,0,0,0.2);
            z-index: 9999;
        ">
            <iframe src="{embed_url}" width="100%" height="100%" frameborder="0"></iframe>
        </div>
    `;
    document.body.appendChild(chatWidget);
    
    var toggle = document.getElementById('chat-toggle');
    var frame = document.getElementById('chat-frame');
    var isOpen = false;
    
    toggle.addEventListener('click', function() {{
        isOpen = !isOpen;
        frame.style.display = isOpen ? 'block' : 'none';
    }});
}})();
</script>''',
            
            "react": f'''// React Component
import React, {{ useState }} from 'react';

const ChatWidget = () => {{
    const [isOpen, setIsOpen] = useState(false);
    
    return (
        <>
            <button 
                onClick={{() => setIsOpen(!isOpen)}}
                style={{{{
                    position: 'fixed',
                    bottom: 20,
                    right: 20,
                    width: 60,
                    height: 60,
                    borderRadius: '50%',
                    background: '{bot.accent_color}',
                    border: 'none',
                    cursor: 'pointer',
                    boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                    zIndex: 9998
                }}}}
            >
                💬
            </button>
            {{isOpen && (
                <div style={{{{
                    position: 'fixed',
                    bottom: 90,
                    right: 20,
                    width: 380,
                    height: 520,
                    borderRadius: 12,
                    overflow: 'hidden',
                    boxShadow: '0 4px 24px rgba(0,0,0,0.2)',
                    zIndex: 9999
                }}}}>
                    <iframe 
                        src="{embed_url}"
                        width="100%"
                        height="100%"
                        frameBorder="0"
                    />
                </div>
            )}}
        </>
    );
}};

export default ChatWidget;'''
        }
    }
