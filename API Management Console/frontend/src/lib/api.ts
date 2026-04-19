import {
  Bot,
  BotCreate,
  BotUpdate,
  QuickChipDefinition,
  Template,
  Analytics,
  ChatResponse,
  ToolCatalogItem,
  ToolSettings,
  QuickChipSettings,
} from "@/types/bot";

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
export interface LoginResult {
  access_token: string;
  requires_password_change?: boolean;
}

export async function login(password: string): Promise<LoginResult> {
  const response = await fetch(`${API_BASE}/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "admin", password }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Login failed" }));
    throw new Error(error.detail || "Login failed");
  }

  const data: LoginResult = await response.json();
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
function defaultToolSettings(enabled: boolean = false): ToolSettings {
  return {
    enabled,
    always_require_approval_for_mutating: true,
    max_tool_calls_per_session: 6,
  };
}

function defaultQuickChipSettings(): QuickChipSettings {
  return {
    initial_chips: [],
    allow_ai_suggestions: true,
  };
}

function normalizeQuickChip(chip: any): QuickChipDefinition {
  const legacyVisibility = chip?.visibility === "on_start_only"
    ? "on_start"
    : chip?.visibility === "conditional"
      ? "keywords"
      : chip?.visibility;
  return {
    label: String(chip?.label || ""),
    action_type: chip?.action_type || "suggested_prompt",
    value: String(chip?.value || chip?.label || ""),
    style: chip?.style || "outline",
    visibility_mode: chip?.visibility_mode || legacyVisibility || "always",
    trigger_keywords: Array.isArray(chip?.trigger_keywords) ? chip.trigger_keywords.map((item: unknown) => String(item || "")) : [],
    ai_instruction: String(chip?.ai_instruction || ""),
    sentiment_filter: chip?.sentiment_filter || "any",
    priority: Number(chip?.priority || 0),
    id: chip?.id || undefined,
    payload: chip?.payload || {},
  };
}

function normalizeQuickChipSettings(raw: any): QuickChipSettings {
  const settings = raw || {};
  return {
    initial_chips: Array.isArray(settings.initial_chips) ? settings.initial_chips.map(normalizeQuickChip) : [],
    allow_ai_suggestions: settings.allow_ai_suggestions !== false,
  };
}

function transformBotResponse(data: any): Bot {
  const expirationDate = data.expiration_date ? new Date(data.expiration_date) : null;
  const isExpired = !!(expirationDate && expirationDate < new Date());
  const toolSettings = data.tool_settings || defaultToolSettings(data.tools_enabled || false);
  const quickChipSettings = normalizeQuickChipSettings(data.quick_chip_settings || defaultQuickChipSettings());

  return {
    id: data.id,
    name: data.name,
    template_type: data.template_type,
    provider_type: data.provider_type,
    system_prompt: data.system_prompt || "",
    welcome_message: data.welcome_message || "",
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
    tools_enabled: !!toolSettings.enabled,
    allowed_tools: data.allowed_tools || [],
    tool_settings: toolSettings,
    quick_chip_settings: quickChipSettings,
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

export type ChatProgressStage =
  | "prepare_request"
  | "opening_session"
  | "searching_knowledge_base"
  | "checking_module_references"
  | "warming_provider"
  | "awaiting_response"
  | "streaming_answer"
  | "retrying_request"
  | "completed";

export interface ChatProgressUpdate {
  stage: ChatProgressStage;
  attempt: number;
  maxAttempts: number;
  message: string;
}

export interface SendMessageOptions {
  maxRetries?: number;
  onProgress?: (update: ChatProgressUpdate) => void;
  onChunk?: (chunk: string) => void;
}

function isRetryableStatus(status: number): boolean {
  return status === 408 || status === 409 || status === 425 || status === 429 || status >= 500;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function streamChatRequest(
  url: string,
  payload: Record<string, string>,
  options?: SendMessageOptions,
  authenticated: boolean = false
): Promise<ChatResponse> {
  const maxAttempts = Math.max(1, (options?.maxRetries ?? 2) + 1);
  let lastError: Error | null = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const hasSession = !!(payload.session_id && payload.session_id.trim());
    options?.onProgress?.({
      stage: hasSession ? "prepare_request" : "opening_session",
      attempt,
      maxAttempts,
      message: hasSession ? "Preparing request..." : "Opening a new chat session...",
    });

    try {
      const headers: HeadersInit = { "Content-Type": "application/json" };
      if (authenticated && authToken) {
        headers["Authorization"] = `Bearer ${authToken}`;
      }

      const response = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });

      if (response.status === 401) {
        clearAuthToken();
        window.location.href = "/login";
        throw new Error("Unauthorized");
      }

      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: "Chat failed" }));
        const errorMessage = error.detail || `Chat failed (status ${response.status})`;
        const requestError = new Error(errorMessage) as Error & { retryable?: boolean };
        requestError.retryable = isRetryableStatus(response.status);
        throw requestError;
      }

      if (!response.body) {
        throw new Error("Streaming response body unavailable");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let accumulated = "";
      let completedPayload: ChatResponse | null = null;

      while (true) {
        const result = await reader.read();
        buffer += decoder.decode(result.value || new Uint8Array(), { stream: !result.done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line) continue;
          const event = JSON.parse(line) as Record<string, any>;
          if (event.type === "status") {
            options?.onProgress?.({
              stage: String(event.stage || "awaiting_response") as ChatProgressStage,
              attempt,
              maxAttempts,
              message: String(event.message || "Working..."),
            });
            continue;
          }
          if (event.type === "chunk") {
            const chunk = String(event.content || "");
            accumulated += chunk;
            options?.onChunk?.(chunk);
            continue;
          }
          if (event.type === "complete") {
            completedPayload = {
              ...(event as ChatResponse),
              response: String(event.response || accumulated),
            };
          }
        }

        if (result.done) {
          break;
        }
      }

      if (!completedPayload) {
        throw new Error("Chat stream ended without a completion payload");
      }

      options?.onProgress?.({
        stage: "completed",
        attempt,
        maxAttempts,
        message: "Response received.",
      });
      return completedPayload;
    } catch (error) {
      const err = (error instanceof Error ? error : new Error("Chat failed")) as Error & { retryable?: boolean };
      lastError = err;
      if (attempt < maxAttempts && err.retryable !== false) {
        options?.onProgress?.({
          stage: "retrying_request",
          attempt,
          maxAttempts,
          message: `Retrying request (${attempt}/${maxAttempts - 1})...`,
        });
        await sleep(400 * attempt);
        continue;
      }
    }
  }

  throw lastError ?? new Error("Chat failed");
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
  source_file_path?: string | null;
  raw_html_path?: string | null;
  line_range?: [number, number] | null;
  paragraph_range?: [number, number] | null;
  created_at: string | null;
  word_count: number;
}

// Chat API
export async function sendMessage(
  botId: string,
  message: string,
  sessionId?: string,
  options?: SendMessageOptions
): Promise<ChatResponse> {
  const payload: Record<string, string> = { message };
  if (sessionId?.trim()) {
    payload.session_id = sessionId.trim();
  }
  return streamChatRequest(`${API_BASE}/bot/${botId}/chat/stream`, payload, options, false);
}

export async function sendAdminPreviewMessage(
  botId: string,
  message: string,
  sessionId?: string,
  options?: SendMessageOptions
): Promise<ChatResponse> {
  const payload: Record<string, string> = { message };
  if (sessionId?.trim()) {
    payload.session_id = sessionId.trim();
  }
  return streamChatRequest(`${API_BASE}/admin/bots/${botId}/chat/stream`, payload, options, true);
}

export async function fetchAdminToolCatalog(): Promise<ToolCatalogItem[]> {
  const response = await fetchWithAuth(`${API_BASE}/admin/tool-catalog`);
  const data = await response.json();
  return data.tools || [];
}

export async function executeAdminToolAction(
  botId: string,
  toolId: string,
  payload: Record<string, unknown> = {},
  sessionId?: string
): Promise<{ status: string; tool_id: string; result: Record<string, unknown>; follow_up: ChatResponse }> {
  const response = await fetchWithAuth(`${API_BASE}/admin/bots/${botId}/tools/execute`, {
    method: "POST",
    body: JSON.stringify({
      tool_id: toolId,
      payload,
      session_id: sessionId,
    }),
  });
  return response.json();
}

export async function triggerPublicToolAction(
  botId: string,
  toolId: string,
  payload: Record<string, unknown> = {},
  sessionId?: string
): Promise<{
  status: string;
  tool_id: string;
  response: string;
  session_id: string;
  quick_chips: ChatResponse["quick_chips"];
  tool_events: ChatResponse["tool_events"];
  pending_tool_actions: ChatResponse["pending_tool_actions"];
  result: Record<string, unknown>;
}> {
  const response = await fetch(`${API_BASE}/bot/${botId}/tools/trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tool_id: toolId,
      payload,
      session_id: sessionId,
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Tool trigger failed" }));
    throw new Error(error.detail || "Tool trigger failed");
  }
  return response.json();
}

export async function fetchBotStatus(botId: string): Promise<{
  bot_id: string;
  name: string;
  is_available: boolean;
  reason?: string | null;
  welcome_message?: string | null;
  startup_welcome_ready?: boolean;
  startup_welcome_message?: string | null;
  startup_welcome_generated_at?: string | null;
  hot_state?: string;
  hot_ready?: boolean;
  provider_ready?: boolean;
  index_hydrated?: boolean;
  warmup_detail?: string | null;
  quick_chip_settings: QuickChipSettings;
}> {
  const response = await fetch(`${API_BASE}/bot/${botId}/status`);
  if (!response.ok) {
    throw new Error("Could not load bot status");
  }
  const data = await response.json();
  return {
    ...data,
    quick_chip_settings: normalizeQuickChipSettings(data.quick_chip_settings || defaultQuickChipSettings()),
  };
}

export interface KnowledgeChunkSource {
  chunk_id: string;
  cleaned_text: string;
  raw_text: string | null;
  raw_html: string | null;
  source_file_path: string | null;
  metadata?: Record<string, unknown> | null;
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

export async function fetchKnowledgeChunkSource(botId: string, chunkId: string): Promise<KnowledgeChunkSource> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${botId}/knowledge/chunks/${chunkId}/source`);
  return response.json();
}

export async function deleteKnowledgeChunk(botId: string, chunkId: string): Promise<void> {
  await fetchWithAuth(`${API_BASE}/bots/${botId}/knowledge/chunks/${chunkId}`, {
    method: "DELETE",
  });
}

export async function updateKnowledgeChunk(botId: string, chunkId: string, content: string): Promise<KnowledgeChunk> {
  const response = await fetchWithAuth(`${API_BASE}/bots/${botId}/knowledge/chunks/${chunkId}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
  return response.json();
}

// Scraper API
export interface ScrapeRunOptions {
  depth?: number;
  limit?: number;
  focus?: string;
  sitemap_only?: boolean;
}

export interface ScraperJobStatus {
  id: string;
  bot_id?: string | null;
  url: string;
  status: string;
  depth: number;
  limit?: number | null;
  focus?: string | null;
  sitemap_only?: boolean;
  pages_scraped: number;
  chunks_created: number;
  error_message?: string | null;
  telemetry?: Record<string, unknown> | null;
  summary?: Record<string, unknown> | null;
}

export async function scrapeWebsite(
  botId: string,
  url: string,
  options: ScrapeRunOptions = {}
): Promise<{ id: string; status: string; limit?: number; focus?: string; sitemap_only?: boolean }> {
  const formData = new FormData();
  formData.append("url", url);
  formData.append("depth", String(options.depth ?? 2));
  formData.append("limit", String(options.limit ?? 20));
  formData.append("focus", options.focus ?? "");
  formData.append("sitemap_only", String(options.sitemap_only ?? false));
  formData.append("consent_confirmed", "true");
  formData.append("append", "true");

  const headers: HeadersInit = {};
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const response = await fetch(`${API_BASE}/bots/${botId}/scrape`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Scrape failed" }));
    throw new Error(error.detail || "Scrape failed");
  }
  const payload = await response.json();
  return {
    id: payload.id || payload.job_id,
    status: payload.status,
    limit: payload.limit,
    focus: payload.focus,
    sitemap_only: payload.sitemap_only,
  };
}

export async function fetchScraperJobStatus(jobId: string): Promise<ScraperJobStatus> {
  const response = await fetchWithAuth(`${API_BASE}/scraper/jobs/${jobId}`);
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
  tunnel_token_required?: boolean;
  tunnel_token?: string | null;
  api_gateway_running?: boolean;
  api_gateway_port?: number | null;
  encryption_enabled?: boolean;
  local_private_execution?: boolean;
  current_deployment_url?: string | null;
  last_system_check_at?: string | null;
  public_tunnel_ready?: boolean;
  tunnel_stable?: boolean;
  uptime_seconds?: number;
  requires_password_change?: boolean;
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
          tunnel_token_required: false,
          tunnel_token: null,
          api_gateway_running: false,
          api_gateway_port: null,
          encryption_enabled: false,
          local_private_execution: true,
          current_deployment_url: "http://localhost:8000/admin",
          last_system_check_at: new Date().toISOString(),
          public_tunnel_ready: false,
          tunnel_stable: false,
          uptime_seconds: 0,
          requires_password_change: false,
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
    tunnel_token_required: false,
    tunnel_token: null,
    api_gateway_running: false,
    api_gateway_port: null,
    encryption_enabled: false,
    local_private_execution: true,
    current_deployment_url: "http://localhost:8000/admin",
    last_system_check_at: null,
    public_tunnel_ready: false,
    tunnel_stable: false,
    uptime_seconds: 0,
    requires_password_change: false,
  };
}

export async function getSetupStatus(): Promise<{
  requires_password_change: boolean;
  encryption_enabled: boolean;
  local_private_execution: boolean;
  current_deployment_url: string | null;
  public_tunnel_ready: boolean;
  tunnel_stable: boolean;
  last_system_check_at: string | null;
  uptime_seconds: number;
}> {
  const response = await fetch(`${API_BASE}/admin/setup-status`);
  if (!response.ok) {
    throw new Error("Could not load setup status");
  }
  return response.json();
}
