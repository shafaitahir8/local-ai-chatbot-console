(function() {
  var containers = document.querySelectorAll('.arctic-chatbot-container');
  if (!containers.length) return;
  var MAX_RETRIES = 2;

  function normalizeApiUrl(url) {
    if (!url) return '';
    var next = String(url).trim();
    if (!/\/api\/bot\/[^/?#]+\/chat([?#].*)?$/i.test(next)) {
      next = next.replace(/\/bot\/([^/?#]+\/chat)([?#].*)?$/i, '/api/bot/$1$2');
    }
    return next;
  }

  function getStatusUrl(apiUrl) {
    if (!apiUrl) return '';
    return apiUrl.replace(/\/chat([?#].*)?$/i, function(_, suffix) {
      return '/status' + (suffix || '');
    });
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

  function isRetryableStatus(statusCode) {
    return statusCode === 408 || statusCode === 409 || statusCode === 425 || statusCode === 429 || statusCode >= 500;
  }

  containers.forEach(function(container) {
    var apiConfig = getApiConfig(container.getAttribute('data-api'));
    if (!apiConfig || !apiConfig.api) return;

    var api = apiConfig.api;
    var statusApi = getStatusUrl(api);

    var messagesEl = container.querySelector('[data-role="messages"]');
    var input = container.querySelector('[data-role="input"]');
    var sendBtn = container.querySelector('[data-role="send"]');
    var statusEl = container.querySelector('[data-role="status"]');
    if (!messagesEl || !input || !sendBtn || !statusEl) return;

    var sessionId = null;
    var sending = false;
    var typingEl = null;
    var startupMessageEl = null;

    function applyCommonHeaders(xhr, includeContentType) {
      if (includeContentType) {
        xhr.setRequestHeader('Content-Type', 'application/json');
      }
      if (apiConfig.isLocalTunnel) {
        xhr.setRequestHeader('bypass-tunnel-reminder', 'true');
      }
      if (apiConfig.tunnelToken) {
        xhr.setRequestHeader('X-Tunnel-Token', apiConfig.tunnelToken);
      }
    }

    function ensureStartupMessage(text) {
      var content = (text || '').trim();
      if (!content) return;

      if (!startupMessageEl || !startupMessageEl.parentNode) {
        var row = document.createElement('div');
        row.className = 'arctic-chatbot-msg arctic-chatbot-msg-bot';

        var bubble = document.createElement('div');
        bubble.className = 'arctic-chatbot-msg-bubble';
        bubble.setAttribute('data-role', 'startup-message');
        row.appendChild(bubble);

        messagesEl.appendChild(row);
        startupMessageEl = bubble;
      }

      startupMessageEl.textContent = content;
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function loadStartupMessage(attempt) {
      if (!statusApi) return;
      var currentAttempt = attempt || 1;
      var xhr = new XMLHttpRequest();
      xhr.open('GET', statusApi, true);
      applyCommonHeaders(xhr, false);

      xhr.onreadystatechange = function() {
        if (xhr.readyState !== 4) return;

        var data = {};
        if (xhr.responseText) {
          try {
            data = JSON.parse(xhr.responseText);
          } catch (e) {
            data = {};
          }
        }

        if (xhr.status >= 200 && xhr.status < 300) {
          var startup = (data.startup_welcome_message || '').trim();
          var configured = (data.welcome_message || '').trim();
          if (startup) {
            ensureStartupMessage(startup);
            return;
          }
          if (configured) {
            ensureStartupMessage(configured);
          }

          if (!startup && data.startup_welcome_ready === false && currentAttempt < 5) {
            window.setTimeout(function() {
              loadStartupMessage(currentAttempt + 1);
            }, 700 * currentAttempt);
          }
          return;
        }

        if (currentAttempt < 3) {
          window.setTimeout(function() {
            loadStartupMessage(currentAttempt + 1);
          }, 500 * currentAttempt);
        }
      };

      xhr.onerror = function() {
        if (currentAttempt < 3) {
          window.setTimeout(function() {
            loadStartupMessage(currentAttempt + 1);
          }, 500 * currentAttempt);
        }
      };

      xhr.send();
    }

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

    function setStatus(text, thinking) {
      statusEl.textContent = text;
      statusEl.className = thinking
        ? 'arctic-chatbot-status arctic-chatbot-thinking'
        : 'arctic-chatbot-status';
    }

    function setLoading(state, statusText) {
      sending = state;
      sendBtn.disabled = state;
      input.disabled = state;
      if (state) {
        setStatus(statusText || 'Working...', true);
      } else {
        setStatus('Online', false);
      }
    }

    function scheduleRetry(nextAttempt, callback) {
      setStatus('Retrying request...', true);
      window.setTimeout(callback, 350 * nextAttempt);
    }

    function sendMessageRequest(msg, attempt, done) {
      var isNewSession = !sessionId;
      setStatus(isNewSession ? 'Opening session...' : 'Sending request...', true);

      var payload = { message: msg };
      if (sessionId) {
        payload.session_id = sessionId;
      }

      setStatus('Waiting for model response...', true);

      var xhr = new XMLHttpRequest();
      xhr.open('POST', api, true);
      applyCommonHeaders(xhr, true);

      xhr.onreadystatechange = function() {
        if (xhr.readyState !== 4) return;

        var data = {};
        if (xhr.responseText) {
          try {
            data = JSON.parse(xhr.responseText);
          } catch (e) {
            data = {};
          }
        }

        if (xhr.status >= 200 && xhr.status < 300) {
          done(null, data);
          return;
        }

        if (attempt <= MAX_RETRIES && isRetryableStatus(xhr.status)) {
          scheduleRetry(attempt, function() {
            sendMessageRequest(msg, attempt + 1, done);
          });
          return;
        }

        var detail = data.detail || ('Server returned status ' + xhr.status);
        done(new Error(detail));
      };

      xhr.onerror = function() {
        if (attempt <= MAX_RETRIES) {
          scheduleRetry(attempt, function() {
            sendMessageRequest(msg, attempt + 1, done);
          });
          return;
        }
        done(new Error('Could not reach the server.'));
      };

      xhr.send(JSON.stringify(payload));
    }

    function sendMessage() {
      if (sending) return;
      var msg = input.value.trim();
      if (!msg) return;

      addMessage(msg, 'user');
      input.value = '';
      setLoading(true, 'Preparing request...');
      addTyping();

      sendMessageRequest(msg, 1, function(err, data) {
        removeTyping();

        if (err) {
          addMessage('Error: ' + err.message, 'bot');
          setStatus('Error', false);
          setLoading(false);
          input.focus();
          return;
        }

        addMessage(data.response || 'No response received.', 'bot');
        if (data.session_id) {
          sessionId = data.session_id;
        }

        setLoading(false);
        input.focus();
      });
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

    loadStartupMessage(1);
  });
})();

