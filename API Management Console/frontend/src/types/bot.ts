export type BotStatus = "active" | "inactive" | "expired";

export interface Bot {
  id: string;
  name: string;
  template_type: string;
  provider_type: string;
  system_prompt: string;
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
}

export interface BotCreate {
  name: string;
  template_type: string;
  provider_type: string;
  system_prompt?: string;
  temperature?: number;
  memory_enabled?: boolean;
  message_limit?: number | null;
  expires_at?: string | null;
  accent_color?: string;
  api_key?: string;
  model?: string;
  base_url?: string;
}

export interface BotUpdate {
  name?: string;
  system_prompt?: string;
  temperature?: number;
  memory_enabled?: boolean;
  message_limit?: number | null;
  expiration_date?: string | null;
  is_active?: boolean;
  accent_color?: string;
  api_key?: string;
  model?: string;
  base_url?: string;
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

export interface ChatResponse {
  response: string;
  session_id: string;
  tokens_used: number;
  latency_ms: number;
  retry_count?: number;
  fallback_used?: boolean;
  status_message?: string | null;
}
