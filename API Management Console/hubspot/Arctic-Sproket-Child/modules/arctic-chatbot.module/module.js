(function() {
  var containers = document.querySelectorAll('.arctic-chatbot-container');
  if (!containers.length) return;

  var ACTIVE_PUBLIC_ORIGIN = 'https://blah-tournament-tech-flags.trycloudflare.com';

  function normalizeApiUrl(url) {
    if (!url) return '';
    var next = String(url).trim();
    if (!/\/api\/bot\/[^/?#]+\/chat([?#].*)?$/i.test(next)) {
      next = next.replace(/\/bot\/([^/?#]+\/chat)([?#].*)?$/i, '/api/bot/$1$2');
    }
    next = next.replace(/^https?:\/\/localhost:8000/i, ACTIVE_PUBLIC_ORIGIN);
    return next;
  }

  function getApiConfig(url) {
    var api = normalizeApiUrl(url);
    if (!api) return null;

    var config = {
      api: api,
      isLocalTunnel: false,
      tunnelToken: ''
    };

    try {
      var parsed = new URL(api, window.location.href);
      var host = (parsed.hostname || '').toLowerCase();
      config.isLocalTunnel = /\.loca\.lt$/.test(host) || /localtunnel\.me$/.test(host);
      config.tunnelToken = parsed.searchParams.get('token') || '';
    } catch (e) {
      config.isLocalTunnel = /https:\/\/[^/]*\.loca\.lt\//i.test(api);
    }

    return config;
  }

  containers.forEach(function(container) {
    var apiConfig = getApiConfig(container.getAttribute('data-api'));
    if (!apiConfig || !apiConfig.api) return;
    var api = apiConfig.api;

    var messagesEl = container.querySelector('[data-role="messages"]');
    var input = container.querySelector('[data-role="input"]');
    var sendBtn = container.querySelector('[data-role="send"]');
    var statusEl = container.querySelector('[data-role="status"]');
    if (!messagesEl || !input || !sendBtn || !statusEl) return;

    var sessionId = 'hs-' + Math.random().toString(36).substring(2, 12);
    var sending = false;
    var typingEl = null;

    function addMessage(text, sender) {
      var div = document.createElement('div');
      div.className = 'arctic-chatbot-msg arctic-chatbot-msg-' + sender;
      var bubble = document.createElement('div');
      bubble.className = 'arctic-chatbot-msg-bubble';
      bubble.textContent = text;
      div.appendChild(bubble);
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function addTyping() {
      var div = document.createElement('div');
      div.className = 'arctic-chatbot-msg arctic-chatbot-msg-bot';
      var bubble = document.createElement('div');
      bubble.className = 'arctic-chatbot-msg-bubble arctic-chatbot-typing';
      bubble.innerHTML = '<span></span><span></span><span></span>';
      div.appendChild(bubble);
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      typingEl = div;
    }

    function removeTyping() {
      if (typingEl && typingEl.parentNode) {
        typingEl.parentNode.removeChild(typingEl);
      }
      typingEl = null;
    }

    function setLoading(state) {
      sending = state;
      sendBtn.disabled = state;
      input.disabled = state;
      if (state) {
        statusEl.textContent = 'Thinking...';
        statusEl.className = 'arctic-chatbot-status arctic-chatbot-thinking';
      } else {
        statusEl.textContent = 'Online';
        statusEl.className = 'arctic-chatbot-status';
      }
    }

    function sendMessage() {
      if (sending) return;
      var msg = input.value.trim();
      if (!msg) return;

      addMessage(msg, 'user');
      input.value = '';
      setLoading(true);
      addTyping();

      var xhr = new XMLHttpRequest();
      xhr.open('POST', api, true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      if (apiConfig.isLocalTunnel) {
        xhr.setRequestHeader('bypass-tunnel-reminder', 'true');
      }
      if (apiConfig.tunnelToken) {
        xhr.setRequestHeader('X-Tunnel-Token', apiConfig.tunnelToken);
      }
      xhr.onreadystatechange = function() {
        if (xhr.readyState !== 4) return;
        removeTyping();
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            var data = JSON.parse(xhr.responseText);
            addMessage(data.response || 'No response received.', 'bot');
            if (data.session_id) sessionId = data.session_id;
          } catch (e) {
            addMessage('Error: Invalid response from server.', 'bot');
          }
        } else {
          addMessage('Error: Server returned status ' + xhr.status, 'bot');
        }
        setLoading(false);
        input.focus();
      };

      xhr.onerror = function() {
        removeTyping();
        addMessage('Error: Could not reach the server.', 'bot');
        setLoading(false);
        input.focus();
      };

      xhr.send(JSON.stringify({ message: msg, session_id: sessionId }));
    }

    sendBtn.addEventListener('click', function(e) {
      e.preventDefault();
      sendMessage();
    });

    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.keyCode === 13) {
        e.preventDefault();
        sendMessage();
      }
    });
  });
})();
