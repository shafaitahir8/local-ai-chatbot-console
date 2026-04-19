export type BotStatus = "active" | "inactive" | "expired";

export interface ToolDefinition {
  id: string;
  display_name: string;
  description: string;
  parameters: Record<string, unknown>;
  execution_type: "builtin" | "webhook" | "local_script";
  scope: "public" | "admin_only";
  is_mutating: boolean;
  requires_approval: boolean;
  execution_config: Record<string, unknown>;
}

export interface ToolSettings {
  enabled: boolean;
  always_require_approval_for_mutating: boolean;
  max_tool_calls_per_session: number;
}

export interface QuickChipDefinition {
  label: string;
  action_type: "suggested_prompt" | "tool_call" | "link";
  value: string;
  style: "primary" | "outline" | "ghost";
  visibility_mode: "always" | "on_start" | "keywords" | "ai_only";
  trigger_keywords: string[];
  ai_instruction: string;
  sentiment_filter: "positive" | "negative" | "any";
  priority: number;
  id?: string | null;
  payload?: Record<string, unknown>;
}

export interface QuickChipSettings {
  initial_chips: QuickChipDefinition[];
  allow_ai_suggestions: boolean;
}

export interface Bot {
  id: string;
  name: string;
  template_type: string;
  provider_type: string;
  system_prompt: string;
  welcome_message?: string;
  status: BotStatus;
  temperature: number;
  memory_enabled: boolean;
  message_limit: number | null;
  messages_used: number;
  token_usage: number;
  avg_latency: number;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  accent_color: string;
  model?: string;
  base_url?: string;
  tools_enabled: boolean;
  allowed_tools: ToolDefinition[];
  tool_settings: ToolSettings;
  quick_chip_settings: QuickChipSettings;
}

export interface BotCreate {
  name: string;
  template_type: string;
  provider_type: string;
  system_prompt?: string;
  welcome_message?: string;
  temperature?: number;
  memory_enabled?: boolean;
  message_limit?: number | null;
  expires_at?: string | null;
  accent_color?: string;
  api_key?: string;
  model?: string;
  base_url?: string;
  tools_enabled?: boolean;
  allowed_tools?: ToolDefinition[];
  tool_settings?: ToolSettings;
  quick_chip_settings?: QuickChipSettings;
}

export interface BotUpdate {
  name?: string;
  system_prompt?: string;
  welcome_message?: string;
  temperature?: number;
  memory_enabled?: boolean;
  message_limit?: number | null;
  expiration_date?: string | null;
  is_active?: boolean;
  accent_color?: string;
  api_key?: string;
  model?: string;
  base_url?: string;
  tools_enabled?: boolean;
  allowed_tools?: ToolDefinition[];
  tool_settings?: ToolSettings;
  quick_chip_settings?: QuickChipSettings;
}

export interface Template {
  id: string;
  name: string;
  provider: string;
  required_fields: string[];
  default_model: string;
  supports_memory: boolean;
  supports_temperature: boolean;
  default_temp: number;
  default_prompt: string;
  description: string;
  icon: string;
}

export interface Analytics {
  total_messages: number;
  total_tokens: number;
  avg_latency: number;
  active_bots: number;
  daily_usage: { date: string; messages: number; tokens: number }[];
  top_questions: { question: string; count: number }[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export type QuickChip = QuickChipDefinition & { id: string };

export interface ToolEvent {
  tool_id: string;
  status: string;
  mutating?: boolean;
  section?: string;
}

export interface PendingToolAction {
  id: string;
  tool_id: string;
  label: string;
  summary: string;
  payload?: Record<string, unknown>;
  requires_approval: boolean;
  mutating: boolean;
  execution_type?: ToolDefinition["execution_type"];
  scope?: ToolDefinition["scope"];
}

export type ToolCatalogItem = ToolDefinition & { is_builtin?: boolean };

export interface ChatResponse {
  response: string;
  session_id: string;
  tokens_used: number;
  latency_ms: number;
  retry_count?: number;
  fallback_used?: boolean;
  status_message?: string | null;
  degraded_components?: string[];
  data_freshness?: string;
  safe_modes?: string[];
  context_scope?: string | null;
  context_sources?: string[];
  citations?: Array<{
    label: string;
    scope?: string | null;
    snippet?: string | null;
    score?: number | null;
  }>;
  quick_chips?: QuickChip[];
  tool_events?: ToolEvent[];
  pending_tool_actions?: PendingToolAction[];
}
