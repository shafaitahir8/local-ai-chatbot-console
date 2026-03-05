import { Bot, BotCreate, BotUpdate, Template, Analytics, ChatResponse } from "@/types/bot";

const API_BASE = "/api";

// Auth token management
let authToken: string | null = localStorage.getItem("auth_token");

export function setAuthToken(token: string) {
  authToken = token;
  localStorage.setItem("auth_token", token);
}

export function clearAuthToken() {
  authToken = null;
  localStorage.removeItem("auth_token");
}

export function getAuthToken() {
  return authToken;
}

async function fetchWithAuth(url: string, options: RequestInit = {}) {
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const response = await fetch(url, { ...options, headers });
  
  if (response.status === 401) {
    clearAuthToken();
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(error.detail || "Request failed");
  }
  
  return response;
}

// Auth API
export async function login(password: string): Promise<{ access_token: string }> {
  const response = await fetch(`${API_BASE}/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "admin", password }),
  });
  
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Login failed" }));
    throw new Error(error.detail || "Login failed");
  }
  
  const data = await response.json();
  setAuthToken(data.access_token);
  return data;
}

export async function logout() {
  try {
    await fetchWithAuth(`${API_BASE}/admin/logout`, { method: "POST" });
  } catch (e) {
    // Ignore errors during logout
  }
  clearAuthToken();
}

// Helper to transform API bot response to frontend format
function transformBotResponse(data: any): Bot {
  return {
    id: data.id,
    name: data.name,
    template_type: data.template_type,
    provider_type: data.provider_type,
    system_prompt: data.system_prompt || "",
    status: data.is_active ? "active" : (data.expiration_date && new Date(data.expiration_date) < new Date() ? "expired" : "inactive"),
    temperature: data.temperature,
    memory_enabled: data.memory_enabled || false,
    message_limit: data.message_limit,
    messages_used: data.messages_used || 0,
    token_usage: data.token_usage || 0,
    avg_latency: data.avg_latency || 0,
    expires_at: data.expiration_date || null,
    created_at: data.created_at,
    updated_at: data.updated_at,
    accent_color: data.accent_color || "#00d4aa",
    model: data.model,
    base_url: data.base_url,
  };
}

// Bot API
export async function fetchBots(): Promise<{ bots: Bot[]; total: number }> {
  const response = await fetchWithAuth(`${API_BASE}/bots`);
  const data = await response.json();
  return {
    bots: data.bots.map(transformBotResponse),
    total: data.total,
  };
}

export async function fetchBot(id: string): Promise<Bot> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${id}`);
  const data = await response.json();
  return transformBotResponse(data);
}

export async function createBot(data: BotCreate): Promise<Bot> {
  const response = await fetchWithAuth(`${API_BASE}/bots`, {
    method: "POST",
    body: JSON.stringify(data),
  });
  const result = await response.json();
  return transformBotResponse(result);
}

export async function updateBot(id: string, data: BotUpdate): Promise<Bot> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  const result = await response.json();
  return transformBotResponse(result);
}

export async function deleteBot(id: string): Promise<void> {
  await fetchWithAuth(`${API_BASE}/bots/${id}`, { method: "DELETE" });
}

export async function cloneBot(id: string, newName?: string): Promise<Bot> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${id}/clone`, {
    method: "POST",
    body: JSON.stringify({ new_name: newName }),
  });
  const result = await response.json();
  return transformBotResponse(result);
}

export async function toggleBot(id: string): Promise<Bot> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${id}/toggle`, {
    method: "POST",
  });
  const result = await response.json();
  return transformBotResponse(result);
}

// Templates API
export async function fetchTemplates(): Promise<Template[]> {
  const response = await fetchWithAuth(`${API_BASE}/templates`);
  const data = await response.json();
  
  // Template icons/descriptions based on provider
  const providerMeta: Record<string, { icon: string; description: string }> = {
    openai: { icon: "🧠", description: "Cloud-based GPT models via OpenAI API" },
    local: { icon: "🖥️", description: "Run models locally via Ollama" },
    claude: { icon: "🔮", description: "Anthropic Claude models" },
  };
  
  // Transform API response to frontend Template type
  const templates = (data.templates || []).map((t: any) => ({
    id: t.name?.toLowerCase().replace(/\s+/g, '_') || t.provider,
    name: t.name || t.provider,
    provider: t.provider,
    required_fields: t.required_fields || [],
    default_model: t.default_model || "",
    supports_memory: t.supports_memory ?? true,
    supports_temperature: t.supports_temperature ?? true,
    default_temp: t.default_temperature ?? 0.7,
    default_prompt: t.default_system_prompt || "You are a helpful assistant.",
    description: providerMeta[t.provider]?.description || `${t.provider} provider`,
    icon: providerMeta[t.provider]?.icon || "🤖",
  }));
  
  return templates;
}

// Analytics API
export async function fetchAnalytics(botId?: string): Promise<Analytics> {
  const url = botId 
    ? `${API_BASE}/admin/analytics/${botId}` 
    : `${API_BASE}/admin/analytics/all`;
  const response = await fetchWithAuth(url);
  const data = await response.json();
  
  // Transform the response to match our Analytics type
  return {
    total_messages: data.total_messages || 0,
    total_tokens: data.total_tokens || 0,
    avg_latency: data.avg_latency_ms ? data.avg_latency_ms / 1000 : 0,
    active_bots: data.active_bots || 0,
    daily_usage: data.daily_usage || [],
    top_questions: data.top_questions || [],
  };
}

// Chat API
export async function sendMessage(botId: string, message: string, sessionId?: string): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE}/bot/${botId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Chat failed" }));
    throw new Error(error.detail || "Chat failed");
  }
  
  return response.json();
}

// Knowledge Base API
export async function uploadDocument(botId: string, file: File): Promise<void> {
  const formData = new FormData();
  formData.append("file", file);
  
  const headers: HeadersInit = {};
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }
  
  const response = await fetch(`${API_BASE}/bots/${botId}/knowledge/file`, {
    method: "POST",
    headers,
    body: formData,
  });
  
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(error.detail || "Upload failed");
  }
}

export async function rebuildEmbeddings(botId: string): Promise<void> {
  await fetchWithAuth(`${API_BASE}/bots/${botId}/rebuild-embeddings`, {
    method: "POST",
  });
}

// Scraper API
export async function scrapeWebsite(botId: string, url: string, depth: number = 2): Promise<{ id: string; status: string }> {
  const response = await fetchWithAuth(`${API_BASE}/scraper/start`, {
    method: "POST",
    body: JSON.stringify({ 
      url, 
      depth, 
      consent_confirmed: true,
      bot_id: botId 
    }),
  });
  return response.json();
}

// Admin API
export async function updatePassword(oldPassword: string, newPassword: string): Promise<void> {
  await fetchWithAuth(`${API_BASE}/admin/change-password?old_password=${encodeURIComponent(oldPassword)}&new_password=${encodeURIComponent(newPassword)}`, {
    method: "POST",
  });
}

export async function getSystemStatus(): Promise<{
  server: string;
  database: string;
  faiss: string;
  openai: string;
  local_llm: string;
}> {
  // Check the system status endpoint
  try {
    const response = await fetchWithAuth(`${API_BASE}/admin/system-status`);
    if (response.ok) {
      return response.json();
    }
  } catch (e) {
    // Fallback to health check
    try {
      const response = await fetch("/health");
      if (response.ok) {
        return {
          server: "Running",
          database: "Connected",
          faiss: "Loaded",
          openai: "Configured",
          local_llm: "Not configured",
        };
      }
    } catch (e) {
      // Ignore
    }
  }
  return {
    server: "Unknown",
    database: "Unknown",
    faiss: "Unknown",
    openai: "Unknown",
    local_llm: "Unknown",
  };
}
