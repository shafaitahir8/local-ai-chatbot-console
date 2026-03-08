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
  const expirationDate = data.expiration_date ? new Date(data.expiration_date) : null;
  const isExpired = !!(expirationDate && expirationDate < new Date());

  return {
    id: data.id,
    name: data.name,
    template_type: data.template_type,
    provider_type: data.provider_type,
    system_prompt: data.system_prompt || "",
    status: isExpired ? "expired" : (data.is_active ? "active" : "inactive"),
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

export async function expireBot(id: string): Promise<Bot> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${id}/expire`, {
    method: "POST",
  });
  const result = await response.json();
  return transformBotResponse(result);
}

export async function recoverBot(id: string): Promise<Bot> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${id}/recover`, {
    method: "POST",
  });
  const result = await response.json();
  return transformBotResponse(result);
}

export async function getBotModelFilesFolder(id: string): Promise<{ bot_id: string; path: string; exists: boolean }> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${id}/model-files`);
  return response.json();
}

export async function openBotModelFilesFolder(id: string): Promise<{ message: string; path: string }> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${id}/model-files/open`, {
    method: "POST",
  });
  return response.json();
}

// Templates API
export async function fetchTemplates(): Promise<Template[]> {
  const response = await fetchWithAuth(`${API_BASE}/templates`);
  const data = await response.json();

  // Template icons/descriptions based on provider
  const providerMeta: Record<string, { icon: string; description: string }> = {
    openai: { icon: "AI", description: "Cloud-based GPT models via OpenAI API" },
    local: { icon: "LOC", description: "Run models locally via Ollama" },
    claude: { icon: "CLD", description: "Anthropic Claude models" },
    gemini: { icon: "GEM", description: "Google Gemini API (free-tier friendly)" },
  };

  // Transform API response to frontend Template type
  const templates = (data.templates || []).map((t: any) => ({
    id: t.template_id || t.name?.toLowerCase().replace(/\s+/g, "_") || t.provider,
    name: t.name || t.provider,
    provider: t.provider,
    required_fields: t.required_fields || [],
    default_model: t.default_model || "",
    supports_memory: t.supports_memory ?? true,
    supports_temperature: t.supports_temperature ?? true,
    default_temp: t.default_temperature ?? 0.7,
    default_prompt: t.default_system_prompt || "You are a helpful assistant.",
    description: providerMeta[t.provider]?.description || `${t.provider} provider`,
    icon: providerMeta[t.provider]?.icon || "BOT",
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

export interface KnowledgeChunk {
  id: string;
  content: string;
  full_content: string;
  source: string | null;
  metadata?: Record<string, unknown> | null;
  created_at: string | null;
  word_count: number;
}

export async function fetchKnowledgeChunks(
  botId: string,
  page: number = 1,
  perPage: number = 20,
  search: string = ""
): Promise<{
  chunks: KnowledgeChunk[];
  pagination: {
    page: number;
    per_page: number;
    total_items: number;
    total_pages: number;
  };
}> {
  const params = new URLSearchParams({
    page: String(page),
    per_page: String(perPage),
  });
  if (search.trim()) params.set("search", search.trim());

  const response = await fetchWithAuth(`${API_BASE}/bots/${botId}/knowledge/chunks?${params.toString()}`);
  return response.json();
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

export interface AdminApiKeyRecord {
  id: string;
  bot_id: string;
  bot_name: string;
  provider: string;
  company: string;
  model?: string | null;
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  masked_key: string;
  key_value?: string | null;
  key_available: boolean;
  decrypt_error?: boolean;
}

export async function fetchApiKeys(): Promise<{ keys: AdminApiKeyRecord[]; total: number; revealed: boolean }> {
  const response = await fetchWithAuth(`${API_BASE}/admin/api-keys`);
  return response.json();
}

export async function revealApiKeys(password: string): Promise<{ keys: AdminApiKeyRecord[]; total: number; revealed: boolean }> {
  const response = await fetchWithAuth(`${API_BASE}/admin/api-keys/reveal`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  return response.json();
}

export async function getSystemStatus(): Promise<{
  server: string;
  database: string;
  faiss: string;
  openai: string;
  local_llm: string;
  cloudflare_tunnel?: string;
  public_base_url?: string | null;
  cloudflare_stable?: boolean;
  cloudflare_source?: string;
  cloudflare_error?: string | null;
  api_gateway_running?: boolean;
  api_gateway_port?: number | null;
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
          cloudflare_tunnel: "Unknown",
          public_base_url: null,
          cloudflare_stable: false,
          cloudflare_source: "unknown",
          cloudflare_error: null,
          api_gateway_running: false,
          api_gateway_port: null,
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
    cloudflare_tunnel: "Unknown",
    public_base_url: null,
    cloudflare_stable: false,
    cloudflare_source: "unknown",
    cloudflare_error: null,
    api_gateway_running: false,
    api_gateway_port: null,
  };
}
