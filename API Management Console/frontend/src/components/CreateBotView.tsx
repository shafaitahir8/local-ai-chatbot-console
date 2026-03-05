import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { fetchTemplates, createBot } from "@/lib/api";
import { BotCreate } from "@/types/bot";
import { toast } from "sonner";
import { ChevronRight, Check, Loader2 } from "lucide-react";

interface CreateBotViewProps {
  onCreated: () => void;
}

export function CreateBotView({ onCreated }: CreateBotViewProps) {
  const [step, setStep] = useState(0);
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const queryClient = useQueryClient();

  const { data: templates = [] } = useQuery({
    queryKey: ["templates"],
    queryFn: fetchTemplates,
  });

  const template = templates.find((t) => t.id === selectedTemplate);

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
    
    createMutation.mutate({
      name,
      template_type: template.id,
      provider_type: template.provider,
      system_prompt: systemPrompt,
      temperature: template.default_temp,
      memory_enabled: template.supports_memory,
      api_key: apiKey || undefined,
    });
  };

  // Fallback templates if API returns empty
  const displayTemplates = templates.length > 0 ? templates : [
    {
      id: "openai",
      name: "OpenAI GPT",
      provider: "openai",
      required_fields: ["api_key", "model"],
      default_model: "gpt-4.1-mini",
      supports_memory: true,
      supports_temperature: true,
      default_temp: 0.7,
      default_prompt: "You are a helpful assistant.",
      description: "Cloud-based GPT models via OpenAI API",
      icon: "🧠",
    },
    {
      id: "local",
      name: "Local LLM",
      provider: "local",
      required_fields: ["base_url"],
      default_model: "llama3.2",
      supports_memory: true,
      supports_temperature: true,
      default_temp: 0.5,
      default_prompt: "You are a helpful assistant.",
      description: "Run models locally via Ollama",
      icon: "🖥️",
    },
    {
      id: "claude",
      name: "Claude (Anthropic)",
      provider: "claude",
      required_fields: ["api_key", "model"],
      default_model: "claude-sonnet-4-20250514",
      supports_memory: true,
      supports_temperature: true,
      default_temp: 0.7,
      default_prompt: "You are a helpful assistant.",
      description: "Anthropic Claude models",
      icon: "🔮",
    },
  ];

  return (
    <div className="max-w-2xl">
      <h1 className="font-mono text-xl font-bold text-foreground mb-1">Create New Bot</h1>
      <p className="font-mono text-xs text-muted-foreground mb-6">Select a template and configure your chatbot</p>

      {/* Steps indicator */}
      <div className="flex items-center gap-2 mb-8">
        {["Template", "Configure", "Review"].map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            <div className={`w-6 h-6 rounded-full flex items-center justify-center font-mono text-[10px] border ${
              i <= step ? "bg-primary/10 text-primary border-primary/30" : "bg-secondary text-muted-foreground border-border"
            }`}>
              {i < step ? <Check className="w-3 h-3" /> : i + 1}
            </div>
            <span className={`font-mono text-xs ${i <= step ? "text-foreground" : "text-muted-foreground"}`}>{s}</span>
            {i < 2 && <ChevronRight className="w-3 h-3 text-muted-foreground" />}
          </div>
        ))}
      </div>

      {step === 0 && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {displayTemplates.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setSelectedTemplate(t.id);
                setSystemPrompt(t.default_prompt);
              }}
              className={`bg-card border rounded-lg p-4 text-left transition-all ${
                selectedTemplate === t.id
                  ? "border-primary/50 glow-primary"
                  : "border-border hover:border-primary/20"
              }`}
            >
              <div className="text-2xl mb-2">{t.icon}</div>
              <h3 className="font-mono text-sm font-semibold text-foreground">{t.name}</h3>
              <p className="font-mono text-[10px] text-muted-foreground mt-1">{t.description}</p>
              <div className="flex gap-1 mt-3">
                {t.supports_memory && <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">MEM</span>}
                <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground">t={t.default_temp}</span>
              </div>
            </button>
          ))}
        </motion.div>
      )}

      {step === 1 && template && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-4">
          <div>
            <label className="font-mono text-[10px] text-muted-foreground uppercase block mb-1">Bot Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Acme Support Bot"
              className="w-full bg-secondary border border-border rounded-md px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors"
            />
          </div>
          {template.required_fields.includes("api_key") && (
            <div>
              <label className="font-mono text-[10px] text-muted-foreground uppercase block mb-1">API Key</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
                className="w-full bg-secondary border border-border rounded-md px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors"
              />
              <p className="font-mono text-[10px] text-muted-foreground mt-1">🔒 Stored encrypted locally. Never sent to frontend.</p>
            </div>
          )}
          <div>
            <label className="font-mono text-[10px] text-muted-foreground uppercase block mb-1">System Prompt</label>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={4}
              className="w-full bg-secondary border border-border rounded-md px-3 py-2 font-mono text-xs text-foreground resize-none focus:outline-none focus:border-primary/50 transition-colors"
            />
          </div>
        </motion.div>
      )}

      {step === 2 && template && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-border rounded-lg p-5 space-y-3">
          <h3 className="font-mono text-sm font-semibold text-foreground mb-3">Review Configuration</h3>
          {[
            { label: "Name", value: name },
            { label: "Template", value: template.name },
            { label: "Provider", value: template.provider },
            { label: "Model", value: template.default_model },
            { label: "Temperature", value: template.default_temp.toString() },
            { label: "Memory", value: template.supports_memory ? "Enabled" : "Disabled" },
          ].map((r) => (
            <div key={r.label} className="flex justify-between py-1 border-b border-border last:border-0">
              <span className="font-mono text-xs text-muted-foreground">{r.label}</span>
              <span className="font-mono text-xs text-foreground">{r.value}</span>
            </div>
          ))}
        </motion.div>
      )}

      <div className="flex justify-between mt-6">
        <button
          onClick={() => setStep(Math.max(0, step - 1))}
          disabled={step === 0}
          className="font-mono text-xs px-4 py-2 rounded-md border border-border bg-secondary text-secondary-foreground hover:text-foreground disabled:opacity-30 transition-all"
        >
          Back
        </button>
        {step < 2 ? (
          <button
            onClick={() => setStep(step + 1)}
            disabled={(step === 0 && !selectedTemplate) || (step === 1 && !name)}
            className="font-mono text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-30 transition-all"
          >
            Continue
          </button>
        ) : (
          <button
            onClick={handleCreate}
            disabled={createMutation.isPending}
            className="font-mono text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all flex items-center gap-2"
          >
            {createMutation.isPending && <Loader2 className="w-3 h-3 animate-spin" />}
            Deploy Bot →
          </button>
        )}
      </div>
    </div>
  );
}
