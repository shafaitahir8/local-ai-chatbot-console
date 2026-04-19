(function() {
  var containers = document.querySelectorAll(".arctic-chatbot-container");
  if (!containers.length) return;

  var ACTIVE_PUBLIC_ORIGIN = "https://blah-tournament-tech-flags.trycloudflare.com";

  function normalizeApiUrl(url) {
    if (!url) return "";
    var next = String(url).trim();
    if (!/\/api\/bot\/[^/?#]+\/chat(?:\/stream)?([?#].*)?$/i.test(next)) {
      next = next.replace(/\/bot\/([^/?#]+\/chat(?:\/stream)?)([?#].*)?$/i, "/api/bot/$1$2");
    }
    next = next.replace(/^https?:\/\/localhost:8000/i, ACTIVE_PUBLIC_ORIGIN);
    return next;
  }

  function appendToken(url, token) {
    if (!token) return url;
    return url + (url.indexOf("?") === -1 ? "?" : "&") + "token=" + encodeURIComponent(token);
  }

  function getApiConfig(url) {
    var api = normalizeApiUrl(url);
    if (!api) return null;

    try {
      var parsed = new URL(api, window.location.href);
      var botMatch = parsed.pathname.match(/\/api\/bot\/([^/]+)\/chat(?:\/stream)?$/i);
      if (!botMatch) return null;

      var host = (parsed.hostname || "").toLowerCase();
      var token = parsed.searchParams.get("token") || "";
      var origin = parsed.origin;
      var botId = botMatch[1];

      return {
        api: parsed.toString(),
        botId: botId,
        statusUrl: appendToken(origin + "/api/bot/" + botId + "/status", token),
        toolTriggerUrl: appendToken(origin + "/api/bot/" + botId + "/tools/trigger", token),
        isLocalTunnel: /\.loca\.lt$/.test(host) || /localtunnel\.me$/.test(host),
        tunnelToken: token
      };
    } catch (error) {
      return null;
    }
  }

  function buildHeaders(apiConfig, includeJson) {
    var headers = {};
    if (includeJson) {
      headers["Content-Type"] = "application/json";
    }
    if (apiConfig.isLocalTunnel) {
      headers["bypass-tunnel-reminder"] = "true";
    }
    if (apiConfig.tunnelToken) {
      headers["X-Tunnel-Token"] = apiConfig.tunnelToken;
    }
    return headers;
  }

  function requestJson(url, apiConfig, options) {
    return fetch(url, {
      method: (options && options.method) || "GET",
      headers: buildHeaders(apiConfig, !!(options && options.json)),
      body: options && options.json ? JSON.stringify(options.json) : undefined
    }).then(function(response) {
      return response.text().then(function(text) {
        var data = text ? JSON.parse(text) : {};
        if (!response.ok) {
          var message = (data && data.detail) || ("Request failed with status " + response.status);
          throw new Error(message);
        }
        return data;
      });
    });
  }

  function normalizeChips(rawChips) {
    var chips = Array.isArray(rawChips) ? rawChips : [];
    return chips.map(function(chip, index) {
      var legacyVisibility = chip.visibility;
      var visibilityMode = chip.visibility_mode
        || (legacyVisibility === "on_start_only"
          ? "on_start"
          : legacyVisibility === "conditional"
            ? "keywords"
            : "always");
      return {
        id: chip.id || ("chip-" + (index + 1)),
        label: chip.label || "",
        action_type: chip.action_type || "suggested_prompt",
        value: chip.value || chip.label || "",
        style: chip.style || "outline",
        visibility_mode: visibilityMode,
        trigger_keywords: Array.isArray(chip.trigger_keywords) ? chip.trigger_keywords : [],
        ai_instruction: chip.ai_instruction || "",
        sentiment_filter: chip.sentiment_filter || "any",
        priority: Number(chip.priority || 0),
        payload: chip.payload || {}
      };
    }).filter(function(chip) {
      if (!chip.label) return false;
      if (!chip.value) return false;
      if (chip.visibility_mode === "keywords" && (!chip.trigger_keywords || !chip.trigger_keywords.length) && !String(chip.ai_instruction || "").trim()) return false;
      if (chip.visibility_mode === "ai_only" && !String(chip.ai_instruction || "").trim()) return false;
      return true;
    });
  }

  function scrollToBottom(messagesEl) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setStatus(statusEl, text, isThinking) {
    statusEl.textContent = text;
    statusEl.className = isThinking ? "arctic-chatbot-status arctic-chatbot-thinking" : "arctic-chatbot-status";
  }

  function buildChipPromptPayload(chip) {
    if (!chip || chip.action_type !== "suggested_prompt") {
      return chip && chip.label ? chip.label : "";
    }
    var hiddenValue = String(chip.value || chip.label || "").trim();
    return [
      "Respond as a chatbot in 1-2 short sentences.",
      "Do not write an email, letter, greeting, sign-off, or placeholders.",
      "Give the user the direct answer only.",
      hiddenValue
    ].join("\n");
  }

  function createChipButton(chip, disabled, onClick) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "arctic-chatbot-quick-chip";
    button.setAttribute("data-style", chip.style || "outline");
    button.disabled = !!disabled;
    button.textContent = chip.label;
    button.addEventListener("click", function(event) {
      event.preventDefault();
      onClick(chip);
    });
    return button;
  }

  function renderQuickChips(target, chips, disabled, onClick) {
    if (!target) return;
    target.innerHTML = "";
    normalizeChips(chips).forEach(function(chip) {
      target.appendChild(createChipButton(chip, disabled, onClick));
    });
  }

  function createMessageElement(text, sender, chips, disabled, onChipClick) {
    var wrapper = document.createElement("div");
    wrapper.className = "arctic-chatbot-msg arctic-chatbot-msg-" + sender;

    var stack = document.createElement("div");
    stack.className = "arctic-chatbot-msg-stack";

    var bubble = document.createElement("div");
    bubble.className = "arctic-chatbot-msg-bubble";
    bubble.textContent = text;
    stack.appendChild(bubble);

    var chipRow = document.createElement("div");
    chipRow.className = "arctic-chatbot-quick-actions";
    renderQuickChips(chipRow, chips, disabled, onChipClick);
    stack.appendChild(chipRow);

    wrapper.appendChild(stack);
    return wrapper;
  }

  function createTypingElement() {
    var wrapper = document.createElement("div");
    wrapper.className = "arctic-chatbot-msg arctic-chatbot-msg-bot";

    var stack = document.createElement("div");
    stack.className = "arctic-chatbot-msg-stack";

    var bubble = document.createElement("div");
    bubble.className = "arctic-chatbot-msg-bubble arctic-chatbot-typing";
    bubble.innerHTML = "<span></span><span></span><span></span>";
    stack.appendChild(bubble);
    wrapper.appendChild(stack);
    return wrapper;
  }

  containers.forEach(function(container) {
    var apiConfig = getApiConfig(container.getAttribute("data-api"));
    if (!apiConfig || !apiConfig.api) return;

    var messagesEl = container.querySelector("[data-role='messages']");
    var input = container.querySelector("[data-role='input']");
    var sendBtn = container.querySelector("[data-role='send']");
    var statusEl = container.querySelector("[data-role='status']");
    var seedBubble = container.querySelector("[data-role='seed-bubble']");
    var seedChips = container.querySelector("[data-role='seed-chips']");
    if (!messagesEl || !input || !sendBtn || !statusEl || !seedBubble || !seedChips) return;

    var sessionId = "hs-" + Math.random().toString(36).substring(2, 12);
    var sending = false;
    var typingEl = null;

    function handleChipClick(chip) {
      if (sending) return;
      if (chip.action_type === "suggested_prompt") {
        sendPrompt(buildChipPromptPayload(chip), chip.label);
        return;
      }
      sendPrompt(chip.label, chip.label);
    }

    function setLoading(state, statusText) {
      sending = state;
      container.classList.toggle("is-loading", state);
      sendBtn.disabled = state;
      input.disabled = state;
      setStatus(statusEl, statusText || (state ? "Thinking..." : "Online"), state);
    }

    function appendMessage(text, sender, chips) {
      var message = createMessageElement(text, sender, chips, sending, handleChipClick);
      messagesEl.appendChild(message);
      scrollToBottom(messagesEl);
    }

    function showTyping() {
      typingEl = createTypingElement();
      messagesEl.appendChild(typingEl);
      scrollToBottom(messagesEl);
    }

    function hideTyping() {
      if (typingEl && typingEl.parentNode) {
        typingEl.parentNode.removeChild(typingEl);
      }
      typingEl = null;
    }

    function applyWelcomeState(payload) {
      var welcomeText = (payload && (payload.startup_welcome_message || payload.welcome_message)) || "Hello! How can I help you today?";
      var startupChips = payload && payload.quick_chip_settings ? payload.quick_chip_settings.initial_chips : [];
      seedBubble.textContent = welcomeText;
      renderQuickChips(seedChips, startupChips, sending, handleChipClick);
      if (payload && payload.is_available === false && payload.reason) {
        setStatus(statusEl, payload.reason, false);
        input.disabled = true;
        sendBtn.disabled = true;
      }
    }

    function sendPrompt(prompt, visibleLabel) {
      if (sending) return;
      var outbound = String(prompt || "").trim();
      var visible = String(visibleLabel || prompt || "").trim();
      if (!outbound || !visible) return;

      appendMessage(visible, "user");
      input.value = "";
      setLoading(true, "Thinking...");
      showTyping();

      requestJson(apiConfig.api, apiConfig, {
        method: "POST",
        json: {
          message: outbound,
          session_id: sessionId
        }
      }).then(function(data) {
        hideTyping();
        appendMessage(data.response || "No response received.", "bot", data.quick_chips || []);
        if (data.session_id) {
          sessionId = data.session_id;
        }
      }).catch(function(error) {
        hideTyping();
        appendMessage(error instanceof Error ? error.message : "Could not reach the server.", "bot", []);
      }).finally(function() {
        setLoading(false, "Online");
        input.focus();
      });
    }

    sendBtn.addEventListener("click", function(event) {
      event.preventDefault();
      sendPrompt(input.value);
    });

    input.addEventListener("keydown", function(event) {
      if (event.key === "Enter" || event.keyCode === 13) {
        event.preventDefault();
        sendPrompt(input.value);
      }
    });

    requestJson(apiConfig.statusUrl, apiConfig)
      .then(applyWelcomeState)
      .catch(function() {
        renderQuickChips(seedChips, [], false, handleChipClick);
      });
  });
})();
