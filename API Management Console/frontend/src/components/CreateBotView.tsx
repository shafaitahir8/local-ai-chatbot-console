import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { fetchTemplates, createBot, fetchAdminToolCatalog } from "@/lib/api";
import { BotCreate, ToolCatalogItem, ToolDefinition } from "@/types/bot";
import { toast } from "sonner";
import { ChevronRight, Check, Loader2, Sparkles, ShieldCheck, PlugZap, Upload, UserRound, MessageSquare, Rocket } from "lucide-react";

interface CreateBotViewProps {
  onCreated: () => void;
}

export function CreateBotView({ onCreated }: CreateBotViewProps) {
  const [step, setStep] = useState(0);
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [toolsEnabled, setToolsEnabled] = useState(true);
  const [allowedToolIds, setAllowedToolIds] = useState<string[]>(["ui.suggest_chips"]);
  const queryClient = useQueryClient();

  const { data: templates = [] } = useQuery({
    queryKey: ["templates"],
    queryFn: fetchTemplates,
  });

  const { data: toolCatalog = [] } = useQuery<ToolCatalogItem[]>({
    queryKey: ["admin-tool-catalog"],
    queryFn: fetchAdminToolCatalog,
  });

  const template = templates.find((t) => t.id === selectedTemplate);
  const availableModels = template && Array.isArray((template as TemplateWithModels).available_models)
    ? (template as TemplateWithModels).available_models
    : [];

  const createMutation = useMutation({
    mutationFn: (data: BotCreate) => createBot(data),
    onSuccess: () => {
      toast.success(`Bot "${name}" created!`);
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      onCreated();
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Failed to create bot");
    },
  });

  const handleCreate = () => {
    if (!template) return;

    const selectedTools: ToolDefinition[] = toolsEnabled
      ? toolCatalog
          .filter((tool) => allowedToolIds.includes(tool.id))
          .map((tool) => ({
            id: tool.id,
            display_name: tool.display_name,
            description: tool.description,
            parameters: tool.parameters,
            execution_type: tool.execution_type,
            scope: tool.scope,
            is_mutating: tool.is_mutating,
            requires_approval: tool.requires_approval,
            execution_config: tool.execution_config || {},
          }))
      : [];

    createMutation.mutate({
      name,
      template_type: template.id,
      provider_type: template.provider,
      system_prompt: systemPrompt,
      temperature: template.default_temp,
      memory_enabled: template.supports_memory,
      api_key: apiKey || undefined,
      model: selectedModel.trim() || template.default_model || undefined,
      tools_enabled: toolsEnabled,
      allowed_tools: selectedTools,
      tool_settings: {
        enabled: toolsEnabled,
        always_require_approval_for_mutating: true,
        max_tool_calls_per_session: 6,
      },
      quick_chip_settings: {
        initial_chips: [],
        allow_ai_suggestions: true,
      },
    });
  };

  const toggleAllowedTool = (toolId: string) => {
    setAllowedToolIds((current) => {
      if (current.includes(toolId)) {
        return current.filter((value) => value !== toolId);
      }
      return [...current, toolId];
    });
    setToolsEnabled(true);
  };

  interface TemplateWithModels {
    available_models?: string[];
  }

  const displayTemplates = templates.length > 0 ? templates : [
    {
      id: "openai_template",
      name: "OpenAI GPT",
      provider: "openai",
      required_fields: ["api_key", "model"],
      default_model: "gpt-4.1-mini",
      supports_memory: true,
      supports_temperature: true,
      default_temp: 0.7,
      default_prompt: "You are a helpful assistant.",
      description: "Cloud-based GPT models via OpenAI API",
      icon: "AI",
    },
    {
      id: "local_template",
      name: "Local LLM",
      provider: "local",
      required_fields: ["base_url"],
      default_model: "qwen3:8b",
      supports_memory: true,
      supports_temperature: true,
      default_temp: 0.5,
      default_prompt: "You are a helpful assistant.",
      description: "Run models locally via Ollama",
      icon: "LOC",
    },
    {
      id: "claude_template",
      name: "Claude (Anthropic)",
      provider: "claude",
      required_fields: ["api_key", "model"],
      default_model: "claude-sonnet-4-20250514",
      supports_memory: true,
      supports_temperature: true,
      default_temp: 0.7,
      default_prompt: "You are a helpful assistant.",
      description: "Anthropic Claude models",
      icon: "CLD",
    },
    {
      id: "gemini_template",
      name: "Google Gemini (Free Tier)",
      provider: "gemini",
      required_fields: ["api_key", "model"],
      default_model: "gemini-2.5-flash",
      supports_memory: true,
      supports_temperature: true,
      default_temp: 0.6,
      default_prompt: "You are a helpful assistant.",
      description: "Google Gemini API using a low-cost fast model",
      icon: "GEM",
    },
  ];

  const onboardingSteps = [
    {
      title: "Welcome",
      detail: "What do you want this HubSpot specialist to do?",
      icon: Sparkles,
      done: step > 0 || !!selectedTemplate,
    },
    {
      title: "Trust",
      detail: "Show encryption, deployment mode, and endpoint stability.",
      icon: ShieldCheck,
      done: step > 0,
    },
    {
      title: "Connect",
      detail: "Choose local or cloud provider and validate the setup.",
      icon: PlugZap,
      done: !!selectedTemplate,
    },
    {
      title: "Knowledge",
      detail: "Upload docs, scrape HubSpot pages, or import module and theme files.",
      icon: Upload,
      done: step > 1,
    },
    {
      title: "Specialize",
      detail: "Pick a persona like CMS Developer, RevOps Analyst, or Module Reviewer.",
      icon: UserRound,
      done: !!systemPrompt,
    },
    {
      title: "Test",
      detail: "Run 3 guided prompts with visible source citations.",
      icon: MessageSquare,
      done: step > 1,
    },
    {
      title: "Publish",
      detail: "Copy the embed or API endpoint and confirm the last verified state.",
      icon: Rocket,
      done: false,
    },
  ];

  return (
    <div className="max-w-6xl">
      <div className="mb-6 rounded-[1.25rem] border border-border bg-card p-5 surface-shadow">
        <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Business Summary</p>
        <h1 className="mt-2 text-2xl font-semibold text-foreground">Create a new specialist</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Launch a polished HubSpot-focused assistant with a guided setup flow that keeps the business framing up front
          and the technical detail contained.
        </p>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div>
          <div className="mb-8 flex flex-wrap items-center gap-2">
            {["Template", "Configure", "Review"].map((s, i) => (
              <div key={s} className="flex items-center gap-2">
                <div className={`flex h-7 w-7 items-center justify-center rounded-full border font-mono text-[10px] ${
                  i <= step ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-secondary text-muted-foreground"
                }`}>
                  {i < step ? <Check className="h-3 w-3" /> : i + 1}
                </div>
                <span className={`text-sm ${i <= step ? "text-foreground" : "text-muted-foreground"}`}>{s}</span>
                {i < 2 && <ChevronRight className="h-3 w-3 text-muted-foreground" />}
              </div>
            ))}
          </div>

          {step === 0 && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {displayTemplates.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setSelectedTemplate(t.id);
                setSystemPrompt(t.default_prompt);
                setSelectedModel(t.default_model || "");
              }}
              className={`rounded-[1rem] border p-5 text-left transition-all ${
                selectedTemplate === t.id
                  ? "border-primary/40 bg-primary/5 surface-shadow"
                  : "border-border bg-card hover:border-primary/20"
              }`}
            >
              <div className="mb-3 text-2xl">{t.icon}</div>
              <h3 className="text-base font-semibold text-foreground">{t.name}</h3>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">{t.description}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {t.supports_memory && <span className="rounded-full border border-accent/20 bg-accent/10 px-2 py-0.5 font-mono text-[10px] text-accent">MEMORY</span>}
                <span className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[10px] text-secondary-foreground">t={t.default_temp}</span>
                <span className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[10px] text-secondary-foreground">{t.provider}</span>
              </div>
            </button>
          ))}
        </motion.div>
          )}

          {step === 1 && template && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-4 rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <div>
            <label className="mb-1 block font-mono text-[10px] uppercase text-muted-foreground">Bot Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Acme Support Bot"
              className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
            />
          </div>
          {template.required_fields.includes("api_key") && (
            <div>
              <label className="mb-1 block font-mono text-[10px] uppercase text-muted-foreground">API Key</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Paste your provider key"
                className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
              />
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">Stored encrypted locally. Never sent back to the frontend.</p>
            </div>
          )}
          <div>
            <label className="mb-1 block font-mono text-[10px] uppercase text-muted-foreground">Model</label>
            {availableModels.length > 0 ? (
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
              >
                {availableModels.map((modelName) => (
                  <option key={modelName} value={modelName}>
                    {modelName}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                placeholder={template.default_model || "Enter a model name"}
                className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
              />
            )}
            <p className="mt-1 text-xs text-muted-foreground">
              Choose the model this bot should use at creation time. You can change it later from the bot detail view if needed.
            </p>
          </div>
          <div>
            <label className="mb-1 block font-mono text-[10px] uppercase text-muted-foreground">System Prompt</label>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={5}
              className="w-full resize-none rounded-xl border border-border bg-background px-3 py-2.5 font-mono text-xs text-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
            />
          </div>
          <div className="rounded-xl border border-border bg-secondary/25 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <label className="block font-mono text-[10px] uppercase text-muted-foreground">Tool Calling</label>
                <p className="mt-1 text-sm text-muted-foreground">Enable built-in tools like quick chips and admin-only knowledge actions.</p>
              </div>
              <button
                onClick={() => setToolsEnabled((current) => !current)}
                className={`rounded-xl border px-3 py-2 font-mono text-[10px] uppercase tracking-wide ${
                  toolsEnabled ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-background text-muted-foreground"
                }`}
              >
                {toolsEnabled ? "Enabled" : "Disabled"}
              </button>
            </div>
            <div className="mt-3 grid gap-2">
              {toolCatalog.map((tool) => {
                const selected = allowedToolIds.includes(tool.id);
                return (
                  <button
                    key={tool.id}
                    type="button"
                    onClick={() => toggleAllowedTool(tool.id)}
                    className={`rounded-xl border px-3 py-2 text-left transition-colors ${
                      selected ? "border-primary/30 bg-primary/10" : "border-border bg-background"
                    }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div>
                        <p className="text-sm font-semibold text-foreground">{tool.display_name}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{tool.description}</p>
                      </div>
                      <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                        {tool.scope === "admin_only" ? "Admin" : "Public"}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </motion.div>
          )}

          {step === 2 && template && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Technical Detail</p>
          <h3 className="mt-2 text-lg font-semibold text-foreground">Review configuration</h3>
          <div className="mt-4 space-y-3">
            {[
              { label: "Name", value: name },
              { label: "Template", value: template.name },
              { label: "Provider", value: template.provider },
              { label: "Model", value: selectedModel || template.default_model },
              { label: "Temperature", value: template.default_temp.toString() },
              { label: "Memory", value: template.supports_memory ? "Enabled" : "Disabled" },
              { label: "Tools", value: toolsEnabled ? (allowedToolIds.join(", ") || "Enabled") : "Disabled" },
            ].map((r) => (
              <div key={r.label} className="flex items-center justify-between rounded-2xl border border-border bg-secondary/35 px-4 py-3">
                <span className="font-mono text-[10px] uppercase text-muted-foreground">{r.label}</span>
                <span className="text-sm text-foreground">{r.value}</span>
              </div>
            ))}
          </div>
        </motion.div>
          )}

          <div className="mt-6 flex justify-between">
            <button
              onClick={() => setStep(Math.max(0, step - 1))}
              disabled={step === 0}
              className="rounded-xl border border-border bg-secondary px-4 py-2 text-sm text-secondary-foreground transition-all hover:text-foreground disabled:opacity-30"
            >
              Back
            </button>
            {step < 2 ? (
              <button
                onClick={() => setStep(step + 1)}
                disabled={(step === 0 && !selectedTemplate) || (step === 1 && (!name || !selectedModel.trim()))}
                className="rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground transition-all hover:opacity-90 disabled:opacity-30"
              >
                Continue
              </button>
            ) : (
              <button
                onClick={handleCreate}
                disabled={createMutation.isPending}
                className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground transition-all hover:opacity-90 disabled:opacity-50"
              >
                {createMutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                Deploy Bot
              </button>
            )}
          </div>
        </div>

        <aside className="rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Onboarding Flow</p>
          <h2 className="mt-2 text-lg font-semibold text-foreground">Frictionless client setup</h2>
          <div className="mt-4 space-y-3">
            {onboardingSteps.map((item, index) => (
              <div key={item.title} className="rounded-2xl border border-border bg-secondary/30 px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className={`flex h-8 w-8 items-center justify-center rounded-full border ${
                    item.done ? "border-success/30 bg-success/10 text-success" : "border-border bg-background text-muted-foreground"
                  }`}>
                    {item.done ? <Check className="h-4 w-4" /> : <item.icon className="h-4 w-4" />}
                  </div>
                  <div>
                    <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">{`Step ${index + 1}`}</p>
                    <h3 className="text-sm font-semibold text-foreground">{item.title}</h3>
                  </div>
                </div>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">{item.detail}</p>
              </div>
            ))}
          </div>

          <div className="mt-5 rounded-2xl border border-primary/20 bg-primary/5 p-4">
            <p className="font-mono text-[10px] uppercase tracking-wide text-primary">Guided Test Prompts</p>
            <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
              <li>1. Audit this HubSpot module for accessibility and spacing issues.</li>
              <li>2. Write HubL for a CTA section with responsive fields.</li>
              <li>3. Summarize this page and suggest a stronger conversion CTA.</li>
            </ul>
          </div>
        </aside>
      </div>
    </div>
  );
}
