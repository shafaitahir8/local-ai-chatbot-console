import { ComponentType, ReactNode, useMemo, useState } from "react";
import Editor from "react-simple-code-editor";
import { toast } from "sonner";
import { AlertTriangle, ChevronDown, ChevronUp, MessageSquare, Pencil, Plus, ShieldCheck, Sparkles, Trash2, Wrench } from "lucide-react";
import { Bot, QuickChipDefinition, ToolCatalogItem, ToolDefinition } from "@/types/bot";

interface BotToolingPanelProps {
  bot: Bot;
  toolCatalog: ToolCatalogItem[];
  onChange: (nextBot: Bot) => void;
}

type ToolSectionKey = "registry" | "safety" | "interface";

const emptyJsonObject = "{}";

function cloneToolDefinition(tool: ToolDefinition | ToolCatalogItem): ToolDefinition {
  return {
    id: tool.id,
    display_name: tool.display_name,
    description: tool.description,
    parameters: JSON.parse(JSON.stringify(tool.parameters || { type: "object", properties: {} })),
    execution_type: tool.execution_type,
    scope: tool.scope,
    is_mutating: tool.is_mutating,
    requires_approval: tool.requires_approval,
    execution_config: JSON.parse(JSON.stringify(tool.execution_config || {})),
  };
}

function createEmptyCustomTool(): ToolDefinition {
  return {
    id: "",
    display_name: "",
    description: "",
    parameters: {
      type: "object",
      properties: {},
    },
    execution_type: "webhook",
    scope: "admin_only",
    is_mutating: false,
    requires_approval: false,
    execution_config: {},
  };
}

function createEmptyQuickChip(): QuickChipDefinition {
  return {
    label: "",
    action_type: "suggested_prompt",
    value: "",
    style: "outline",
    visibility_mode: "on_start",
    trigger_keywords: [],
    ai_instruction: "",
    sentiment_filter: "any",
    priority: 0,
    payload: {},
  };
}

function stringifyTriggerKeywords(value: string[]) {
  return (value || []).filter(Boolean).join(", ");
}

function parseTriggerKeywords(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function prettyJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}

function parseJsonObject(label: string, raw: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(raw || emptyJsonObject);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`${label} must be a JSON object.`);
    }
    return parsed as Record<string, unknown>;
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : `${label} is not valid JSON.`);
  }
}

function SectionCard({
  title,
  description,
  icon,
  open,
  onToggle,
  children,
}: {
  title: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const Icon = icon;
  return (
    <div className="rounded-[1rem] border border-border bg-card surface-shadow">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-start justify-between gap-4 px-4 py-4 text-left"
      >
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-full border border-border bg-secondary/45 text-primary">
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">{title}</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">{description}</p>
          </div>
        </div>
        {open ? <ChevronUp className="mt-1 h-4 w-4 text-muted-foreground" /> : <ChevronDown className="mt-1 h-4 w-4 text-muted-foreground" />}
      </button>
      {open && <div className="border-t border-border px-4 py-4">{children}</div>}
    </div>
  );
}

export function BotToolingPanel({ bot, toolCatalog, onChange }: BotToolingPanelProps) {
  const [sectionOpen, setSectionOpen] = useState<Record<ToolSectionKey, boolean>>({
    registry: true,
    safety: true,
    interface: true,
  });
  const [expandedChipIndices, setExpandedChipIndices] = useState<Record<number, boolean>>({});
  const [toolModalOpen, setToolModalOpen] = useState(false);
  const [editingToolId, setEditingToolId] = useState<string | null>(null);
  const [toolDraft, setToolDraft] = useState<ToolDefinition>(createEmptyCustomTool());
  const [toolParametersText, setToolParametersText] = useState(prettyJson(createEmptyCustomTool().parameters));
  const [toolExecutionConfigText, setToolExecutionConfigText] = useState(prettyJson(createEmptyCustomTool().execution_config));

  const builtinToolIds = useMemo(() => new Set(toolCatalog.map((tool) => tool.id)), [toolCatalog]);
  const selectedToolIds = useMemo(() => new Set(bot.allowed_tools.map((tool) => tool.id)), [bot.allowed_tools]);
  const customTools = useMemo(
    () => bot.allowed_tools.filter((tool) => !builtinToolIds.has(tool.id)),
    [bot.allowed_tools, builtinToolIds]
  );
  const localScriptTools = useMemo(
    () => bot.allowed_tools.filter((tool) => tool.execution_type === "local_script"),
    [bot.allowed_tools]
  );

  const updateBot = (mutator: (current: Bot) => Bot) => {
    onChange(mutator(bot));
  };

  const updateToolSettings = (patch: Partial<Bot["tool_settings"]>) => {
    updateBot((current) => {
      const nextSettings = {
        ...current.tool_settings,
        ...patch,
      };
      return {
        ...current,
        tools_enabled: nextSettings.enabled,
        tool_settings: nextSettings,
      };
    });
  };

  const toggleBuiltinTool = (tool: ToolCatalogItem) => {
    updateBot((current) => {
      const alreadySelected = current.allowed_tools.some((entry) => entry.id === tool.id);
      const nextAllowedTools = alreadySelected
        ? current.allowed_tools.filter((entry) => entry.id !== tool.id)
        : [...current.allowed_tools, cloneToolDefinition(tool)];
      const nextSettings = {
        ...current.tool_settings,
        enabled: alreadySelected ? current.tool_settings.enabled : true,
      };
      return {
        ...current,
        tools_enabled: nextSettings.enabled,
        tool_settings: nextSettings,
        allowed_tools: nextAllowedTools,
      };
    });
  };

  const openNewCustomToolModal = () => {
    const emptyTool = createEmptyCustomTool();
    setEditingToolId(null);
    setToolDraft(emptyTool);
    setToolParametersText(prettyJson(emptyTool.parameters));
    setToolExecutionConfigText(prettyJson(emptyTool.execution_config));
    setToolModalOpen(true);
  };

  const openExistingCustomToolModal = (tool: ToolDefinition) => {
    const cloned = cloneToolDefinition(tool);
    setEditingToolId(tool.id);
    setToolDraft(cloned);
    setToolParametersText(prettyJson(cloned.parameters));
    setToolExecutionConfigText(prettyJson(cloned.execution_config));
    setToolModalOpen(true);
  };

  const saveCustomToolDraft = () => {
    const trimmedId = toolDraft.id.trim();
    const trimmedName = toolDraft.display_name.trim();
    const trimmedDescription = toolDraft.description.trim();

    if (!trimmedId || !trimmedName || !trimmedDescription) {
      toast.error("Tool ID, display name, and description are required.");
      return;
    }
    if (builtinToolIds.has(trimmedId)) {
      toast.error("That tool ID is reserved by a built-in tool. Choose a custom slug.");
      return;
    }

    let parsedParameters: Record<string, unknown>;
    let parsedExecutionConfig: Record<string, unknown>;
    try {
      parsedParameters = parseJsonObject("Parameter schema", toolParametersText);
      parsedExecutionConfig = parseJsonObject("Execution config", toolExecutionConfigText);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "JSON is invalid.");
      return;
    }

    let nextTool: ToolDefinition = {
      ...toolDraft,
      id: trimmedId,
      display_name: trimmedName,
      description: trimmedDescription,
      parameters: parsedParameters,
      execution_config: parsedExecutionConfig,
    };

    if (nextTool.execution_type === "local_script") {
      nextTool = {
        ...nextTool,
        scope: "admin_only",
        requires_approval: true,
      };
    }

    if (nextTool.scope === "public") {
      nextTool = {
        ...nextTool,
        is_mutating: false,
        requires_approval: false,
      };
    }

    const duplicate = bot.allowed_tools.find(
      (tool) => tool.id === nextTool.id && tool.id !== editingToolId
    );
    if (duplicate) {
      toast.error("A tool with that ID already exists on this bot.");
      return;
    }

    updateBot((current) => {
      const nextAllowedTools = [
        ...current.allowed_tools.filter((tool) => tool.id !== editingToolId && tool.id !== nextTool.id),
        nextTool,
      ];
      const nextSettings = {
        ...current.tool_settings,
        enabled: true,
      };
      return {
        ...current,
        tools_enabled: true,
        tool_settings: nextSettings,
        allowed_tools: nextAllowedTools,
      };
    });

    setToolModalOpen(false);
    setEditingToolId(null);
    toast.success("Custom tool saved.");
  };

  const deleteCustomTool = (toolId: string) => {
    updateBot((current) => ({
      ...current,
      allowed_tools: current.allowed_tools.filter((tool) => tool.id !== toolId),
    }));
  };

  const updateQuickChip = (index: number, patch: Partial<QuickChipDefinition>) => {
    updateBot((current) => ({
      ...current,
      quick_chip_settings: {
        ...current.quick_chip_settings,
        initial_chips: current.quick_chip_settings.initial_chips.map((chip, chipIndex) =>
          chipIndex === index ? { ...chip, ...patch } : chip
        ),
      },
    }));
  };

  const addInitialChip = () => {
    const nextIndex = bot.quick_chip_settings.initial_chips.length;
    updateBot((current) => ({
      ...current,
      quick_chip_settings: {
        ...current.quick_chip_settings,
        initial_chips: [...current.quick_chip_settings.initial_chips, createEmptyQuickChip()],
      },
    }));
    setExpandedChipIndices((current) => ({
      ...current,
      [nextIndex]: true,
    }));
  };

  const removeInitialChip = (index: number) => {
    updateBot((current) => ({
      ...current,
      quick_chip_settings: {
        ...current.quick_chip_settings,
        initial_chips: current.quick_chip_settings.initial_chips.filter((_, chipIndex) => chipIndex !== index),
      },
    }));
    setExpandedChipIndices((current) => {
      const next: Record<number, boolean> = {};
      Object.entries(current).forEach(([key, value]) => {
        const numericKey = Number(key);
        if (numericKey < index) {
          next[numericKey] = value;
        } else if (numericKey > index) {
          next[numericKey - 1] = value;
        }
      });
      return next;
    });
  };

  const toggleChipExpanded = (index: number) => {
    setExpandedChipIndices((current) => ({
      ...current,
      [index]: !current[index],
    }));
  };

  return (
    <div className="space-y-5">
      <SectionCard
        title="Tool Registry"
        description="Select built-ins, add custom webhooks or local scripts, and keep the bot-specific registry organized."
        icon={Wrench}
        open={sectionOpen.registry}
        onToggle={() => setSectionOpen((current) => ({ ...current, registry: !current.registry }))}
      >
        <div className="space-y-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Available Tools</p>
              <p className="mt-1 text-sm text-muted-foreground">Built-ins stay read-only here. Custom tools use webhook or local script execution.</p>
            </div>
            <button
              type="button"
              onClick={openNewCustomToolModal}
              className="inline-flex items-center gap-2 rounded-xl border border-primary/25 bg-primary/10 px-3 py-2 text-sm text-primary transition-colors hover:border-primary/40 hover:bg-primary/15"
            >
              <Plus className="h-4 w-4" />
              Add Custom Tool
            </button>
          </div>

          <div className="grid gap-3">
            {toolCatalog.map((tool) => {
              const selected = selectedToolIds.has(tool.id);
              return (
                <button
                  key={tool.id}
                  type="button"
                  onClick={() => toggleBuiltinTool(tool)}
                  className={`rounded-2xl border px-4 py-3 text-left transition-colors ${
                    selected ? "border-primary/30 bg-primary/10" : "border-border bg-secondary/20 hover:border-primary/20"
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold text-foreground">{tool.display_name}</p>
                        <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                          {tool.scope === "admin_only" ? "Admin" : "Public"}
                        </span>
                        <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                          {tool.execution_type}
                        </span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">{tool.description}</p>
                    </div>
                    <span className={`rounded-full px-2 py-1 font-mono text-[10px] uppercase tracking-wide ${selected ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground"}`}>
                      {selected ? "Enabled" : "Available"}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>

          <div className="rounded-2xl border border-border bg-secondary/20 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Custom Tools</p>
                <p className="mt-1 text-sm text-muted-foreground">Per-bot definitions that the LLM can call when they’re allowed and relevant.</p>
              </div>
              <span className="rounded-full border border-border px-2 py-1 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                {customTools.length} configured
              </span>
            </div>

            {customTools.length === 0 ? (
              <p className="mt-3 text-sm text-muted-foreground">No custom tools yet. Use webhook tools for external automation and local scripts for desktop-only helpers.</p>
            ) : (
              <div className="mt-3 space-y-3">
                {customTools.map((tool) => (
                  <div key={tool.id} className="rounded-2xl border border-border bg-background px-4 py-3">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-semibold text-foreground">{tool.display_name}</p>
                          <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                            {tool.execution_type}
                          </span>
                          <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                            {tool.scope === "admin_only" ? "Admin" : "Public"}
                          </span>
                          {tool.is_mutating && (
                            <span className="rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-warning">
                              Mutating
                            </span>
                          )}
                        </div>
                        <p className="mt-1 font-mono text-[11px] text-muted-foreground">{tool.id}</p>
                        <p className="mt-2 text-xs leading-5 text-muted-foreground">{tool.description}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => openExistingCustomToolModal(tool)}
                          className="inline-flex items-center gap-1 rounded-lg border border-border bg-secondary/45 px-3 py-1.5 text-xs text-secondary-foreground transition-colors hover:text-foreground"
                        >
                          <Pencil className="h-3 w-3" />
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => deleteCustomTool(tool.id)}
                          className="inline-flex items-center gap-1 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-1.5 text-xs text-destructive transition-colors hover:border-destructive/40 hover:bg-destructive/10"
                        >
                          <Trash2 className="h-3 w-3" />
                          Remove
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title="Safety & Permissions"
        description="Control global tool access, approval posture, and session guardrails."
        icon={ShieldCheck}
        open={sectionOpen.safety}
        onToggle={() => setSectionOpen((current) => ({ ...current, safety: !current.safety }))}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-2xl border border-border bg-secondary/20 p-4">
            <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Enable Tool Calling</p>
            <p className="mt-1 text-sm text-muted-foreground">If disabled, the LLM won’t see any tools even if they stay listed in the registry.</p>
            <button
              type="button"
              onClick={() => updateToolSettings({ enabled: !bot.tool_settings.enabled })}
              className={`mt-3 rounded-xl border px-3 py-2 font-mono text-[10px] uppercase tracking-wide ${
                bot.tool_settings.enabled ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-background text-muted-foreground"
              }`}
            >
              {bot.tool_settings.enabled ? "Enabled" : "Disabled"}
            </button>
          </div>

          <div className="rounded-2xl border border-border bg-secondary/20 p-4">
            <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Always Ask for Approval</p>
            <p className="mt-1 text-sm text-muted-foreground">Keep mutating tools behind an explicit approval card in admin preview.</p>
            <button
              type="button"
              onClick={() =>
                updateToolSettings({
                  always_require_approval_for_mutating: !bot.tool_settings.always_require_approval_for_mutating,
                })
              }
              className={`mt-3 rounded-xl border px-3 py-2 font-mono text-[10px] uppercase tracking-wide ${
                bot.tool_settings.always_require_approval_for_mutating
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "border-border bg-background text-muted-foreground"
              }`}
            >
              {bot.tool_settings.always_require_approval_for_mutating ? "Enabled" : "Disabled"}
            </button>
          </div>

          <div className="rounded-2xl border border-border bg-secondary/20 p-4 md:col-span-2">
            <label className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Max Tool Calls Per Session</label>
            <p className="mt-1 text-sm text-muted-foreground">Stops looping models from repeatedly invoking tools and burning tokens.</p>
            <input
              type="number"
              min={1}
              max={50}
              value={bot.tool_settings.max_tool_calls_per_session}
              onChange={(event) =>
                updateToolSettings({
                  max_tool_calls_per_session: Math.max(1, Math.min(50, Number(event.target.value || 1))),
                })
              }
              className="mt-3 w-full rounded-xl border border-border bg-background px-3 py-2 font-mono text-sm text-foreground focus:border-primary/50 focus:outline-none"
            />
          </div>
        </div>

        <div className="mt-4 rounded-2xl border border-border bg-background/60 p-4">
          <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Runtime Notes</p>
          <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
            <li>Public tools stay advisory only and cannot be marked mutating.</li>
            <li>Admin preview can execute read-only tools immediately and stage mutating tools behind approval.</li>
            <li>Local scripts are always admin-only and approval-gated.</li>
          </ul>
        </div>

        {localScriptTools.length > 0 && (
          <div className="mt-4 flex items-start gap-3 rounded-2xl border border-destructive/25 bg-destructive/5 p-4">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <div>
              <p className="text-sm font-semibold text-foreground">Desktop permissions warning</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Local script tools run with the same permissions as your desktop user. Review every configured path before approving execution.
              </p>
            </div>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Interface Options"
        description="Configure startup chips and decide whether the AI can suggest contextual chips during the conversation."
        icon={MessageSquare}
        open={sectionOpen.interface}
        onToggle={() => setSectionOpen((current) => ({ ...current, interface: !current.interface }))}
      >
        <div className="rounded-2xl border border-border bg-secondary/20 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">AI Suggestions</p>
              <p className="mt-1 text-sm text-muted-foreground">Let the model publish dynamic quick chips through the internal `ui.suggest_chips` tool.</p>
            </div>
            <button
              type="button"
              onClick={() =>
                updateBot((current) => ({
                  ...current,
                  quick_chip_settings: {
                    ...current.quick_chip_settings,
                    allow_ai_suggestions: !current.quick_chip_settings.allow_ai_suggestions,
                  },
                }))
              }
              className={`rounded-xl border px-3 py-2 font-mono text-[10px] uppercase tracking-wide ${
                bot.quick_chip_settings.allow_ai_suggestions
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "border-border bg-background text-muted-foreground"
              }`}
            >
              {bot.quick_chip_settings.allow_ai_suggestions ? "Enabled" : "Disabled"}
            </button>
          </div>
        </div>

        <div className="mt-4 rounded-2xl border border-border bg-secondary/20 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Configured Chips</p>
              <p className="mt-1 text-sm text-muted-foreground">These chips are API-driven. Use on-start chips for opening prompts, keyword chips for deterministic triggers, and AI instructions for contextual suggestions.</p>
            </div>
            <button
              type="button"
              onClick={addInitialChip}
              className="inline-flex items-center gap-2 rounded-xl border border-primary/25 bg-primary/10 px-3 py-2 text-sm text-primary transition-colors hover:border-primary/40 hover:bg-primary/15"
            >
              <Plus className="h-4 w-4" />
              Add Chip
            </button>
          </div>

          {bot.quick_chip_settings.initial_chips.length === 0 ? (
            <div className="mt-4 rounded-2xl border border-dashed border-border bg-background/60 px-4 py-5 text-sm text-muted-foreground">
              No chips configured yet. Add a few opening prompts or intent-driven response chips.
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              {bot.quick_chip_settings.initial_chips.map((chip, index) => (
                <div key={`initial-chip-${index}`} className="rounded-2xl border border-border bg-background px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <button
                      type="button"
                      onClick={() => toggleChipExpanded(index)}
                      className="flex min-w-0 flex-1 items-start gap-3 text-left"
                    >
                      <div className="mt-0.5 rounded-full border border-border bg-secondary/40 p-1 text-muted-foreground">
                        {expandedChipIndices[index] ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Chip {index + 1}</p>
                        <p className="mt-1 truncate text-sm font-semibold text-foreground">{chip.label || "Untitled chip"}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {chip.action_type.replace("_", " ")} - {chip.visibility_mode.replace("_", " ")} - priority {chip.priority}
                        </p>
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={() => removeInitialChip(index)}
                      className="inline-flex items-center gap-1 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-1.5 text-xs text-destructive transition-colors hover:border-destructive/40 hover:bg-destructive/10"
                    >
                      <Trash2 className="h-3 w-3" />
                      Remove
                    </button>
                  </div>
                  {expandedChipIndices[index] && (
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <div>
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Label</label>
                      <input
                        value={chip.label}
                        onChange={(event) => updateQuickChip(index, { label: event.target.value })}
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Action Type</label>
                      <select
                        value={chip.action_type}
                        onChange={(event) =>
                          updateQuickChip(index, {
                            action_type: event.target.value as QuickChipDefinition["action_type"],
                          })
                        }
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      >
                        <option value="suggested_prompt">Suggested Prompt</option>
                        <option value="tool_call">Tool Call</option>
                        <option value="link">Link</option>
                      </select>
                    </div>
                    <div>
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Hidden Prompt / Action Value</label>
                      <input
                        value={chip.value}
                        onChange={(event) => updateQuickChip(index, { value: event.target.value })}
                        placeholder={
                          chip.action_type === "tool_call"
                            ? "Hidden tool identifier"
                            : chip.action_type === "link"
                              ? "Hidden link target"
                              : "Hidden prompt to send"
                        }
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      />
                      <p className="mt-1 text-xs text-muted-foreground">Clicking the chip shows the label in chat. This hidden value is only sent behind the scenes for prompt-driven chips.</p>
                    </div>
                    <div>
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Style</label>
                      <select
                        value={chip.style}
                        onChange={(event) =>
                          updateQuickChip(index, {
                            style: event.target.value as QuickChipDefinition["style"],
                          })
                        }
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      >
                        <option value="primary">Primary</option>
                        <option value="outline">Outline</option>
                        <option value="ghost">Ghost</option>
                      </select>
                    </div>
                    <div>
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Visibility Mode</label>
                      <select
                        value={chip.visibility_mode}
                        onChange={(event) =>
                          updateQuickChip(index, {
                            visibility_mode: event.target.value as QuickChipDefinition["visibility_mode"],
                          })
                        }
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      >
                        <option value="always">Always</option>
                        <option value="on_start">On Start</option>
                        <option value="keywords">Keywords</option>
                        <option value="ai_only">AI Only</option>
                      </select>
                    </div>
                    <div>
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Priority</label>
                      <input
                        type="number"
                        min={0}
                        max={1000}
                        value={chip.priority}
                        onChange={(event) =>
                          updateQuickChip(index, {
                            priority: Math.max(0, Math.min(1000, Number(event.target.value || 0))),
                          })
                        }
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      />
                    </div>
                    <div className="md:col-span-2">
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Trigger Keywords</label>
                      <input
                        value={stringifyTriggerKeywords(chip.trigger_keywords)}
                        onChange={(event) =>
                          updateQuickChip(index, {
                            trigger_keywords: parseTriggerKeywords(event.target.value),
                          })
                        }
                        placeholder="meeting, demo, talk with sales"
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      />
                      <p className="mt-1 text-xs text-muted-foreground">Used by the deterministic chip scanner when visibility mode is set to keywords.</p>
                    </div>
                    <div className="md:col-span-2">
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">AI Instruction</label>
                      <textarea
                        rows={2}
                        value={chip.ai_instruction}
                        onChange={(event) => updateQuickChip(index, { ai_instruction: event.target.value })}
                        placeholder="Suggest when the user asks to speak with a human, set up a meeting, or request a demo."
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Sentiment Filter</label>
                      <select
                        value={chip.sentiment_filter}
                        onChange={(event) =>
                          updateQuickChip(index, {
                            sentiment_filter: event.target.value as QuickChipDefinition["sentiment_filter"],
                          })
                        }
                        className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      >
                        <option value="any">Any</option>
                        <option value="positive">Positive</option>
                        <option value="negative">Negative</option>
                      </select>
                    </div>
                  </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="mt-4 rounded-2xl border border-border bg-background/60 p-4">
          <div className="flex items-start gap-3">
            <Sparkles className="mt-0.5 h-4 w-4 text-primary" />
            <div>
              <p className="text-sm font-semibold text-foreground">Recommended quick-chip pattern</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Keep startup chips focused on safe next steps like suggested prompts, public read-only tool calls, or links. Let the AI own the more contextual chip suggestions mid-conversation.
              </p>
            </div>
          </div>
        </div>
      </SectionCard>

      {toolModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/45 px-4 py-8 backdrop-blur-sm">
          <div className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-[1.25rem] border border-border bg-background shadow-2xl">
            <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">
                  {editingToolId ? "Edit Custom Tool" : "Add Custom Tool"}
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Define the schema the LLM sees and the execution config the backend will use.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setToolModalOpen(false)}
                className="rounded-full border border-border bg-secondary/40 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground"
              >
                Close
              </button>
            </div>

            <div className="space-y-5 px-5 py-5">
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Tool ID</label>
                  <input
                    value={toolDraft.id}
                    onChange={(event) => setToolDraft((current) => ({ ...current, id: event.target.value }))}
                    placeholder="example.tool_slug"
                    className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Display Name</label>
                  <input
                    value={toolDraft.display_name}
                    onChange={(event) => setToolDraft((current) => ({ ...current, display_name: event.target.value }))}
                    placeholder="Readable label for the editor"
                    className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Description</label>
                <textarea
                  value={toolDraft.description}
                  onChange={(event) => setToolDraft((current) => ({ ...current, description: event.target.value }))}
                  rows={3}
                  className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                />
                <p className="mt-1 text-xs text-muted-foreground">This description is the instruction the LLM uses to decide when the tool matters.</p>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Execution Type</label>
                  <select
                    value={toolDraft.execution_type}
                    onChange={(event) =>
                      setToolDraft((current) => ({
                        ...current,
                        execution_type: event.target.value as ToolDefinition["execution_type"],
                        scope: event.target.value === "local_script" ? "admin_only" : current.scope,
                        requires_approval: event.target.value === "local_script" ? true : current.requires_approval,
                      }))
                    }
                    className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                  >
                    <option value="webhook">Webhook</option>
                    <option value="local_script">Local Script</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Scope</label>
                  <select
                    value={toolDraft.scope}
                    onChange={(event) =>
                      setToolDraft((current) => {
                        const nextScope = event.target.value as ToolDefinition["scope"];
                        return {
                          ...current,
                          scope: current.execution_type === "local_script" ? "admin_only" : nextScope,
                          is_mutating: nextScope === "public" ? false : current.is_mutating,
                          requires_approval: nextScope === "public" ? false : current.requires_approval,
                        };
                      })
                    }
                    disabled={toolDraft.execution_type === "local_script"}
                    className="w-full rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none disabled:opacity-50"
                  >
                    <option value="admin_only">Admin Only</option>
                    <option value="public">Public</option>
                  </select>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <button
                  type="button"
                  onClick={() =>
                    setToolDraft((current) => ({
                      ...current,
                      is_mutating: current.scope === "public" ? false : !current.is_mutating,
                    }))
                  }
                  disabled={toolDraft.scope === "public"}
                  className={`rounded-xl border px-3 py-2 text-left disabled:opacity-50 ${
                    toolDraft.is_mutating ? "border-warning/30 bg-warning/10 text-warning" : "border-border bg-secondary/20 text-muted-foreground"
                  }`}
                >
                  <p className="font-mono text-[10px] uppercase tracking-wide">Mutating</p>
                  <p className="mt-1 text-sm">Requires explicit user intent and approval before changing system state.</p>
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setToolDraft((current) => ({
                      ...current,
                      requires_approval:
                        current.execution_type === "local_script" || current.scope === "public"
                          ? current.requires_approval
                          : !current.requires_approval,
                    }))
                  }
                  disabled={toolDraft.execution_type === "local_script" || toolDraft.scope === "public"}
                  className={`rounded-xl border px-3 py-2 text-left disabled:opacity-50 ${
                    toolDraft.requires_approval ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-secondary/20 text-muted-foreground"
                  }`}
                >
                  <p className="font-mono text-[10px] uppercase tracking-wide">Approval Required</p>
                  <p className="mt-1 text-sm">Use this when admins should explicitly confirm the action before it runs.</p>
                </button>
              </div>

              {toolDraft.execution_type === "local_script" && (
                <div className="flex items-start gap-3 rounded-2xl border border-destructive/25 bg-destructive/5 px-4 py-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                  <div>
                    <p className="text-sm font-semibold text-foreground">Desktop execution warning</p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      This script runs with the permissions of the current desktop user. Local scripts stay admin-only and approval-gated even if you try to relax those settings here.
                    </p>
                  </div>
                </div>
              )}

              <div className="grid gap-5 lg:grid-cols-2">
                <div>
                  <label className="mb-2 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Parameters JSON Schema</label>
                  <Editor
                    value={toolParametersText}
                    onValueChange={setToolParametersText}
                    highlight={(code) => code}
                    padding={12}
                    className="min-h-[240px] rounded-2xl border border-border bg-secondary/20 font-mono text-xs text-foreground"
                    style={{ fontFamily: "ui-monospace, SFMono-Regular, SFMono, Menlo, monospace", whiteSpace: "pre-wrap" }}
                  />
                </div>
                <div>
                  <label className="mb-2 block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Execution Config</label>
                  <Editor
                    value={toolExecutionConfigText}
                    onValueChange={setToolExecutionConfigText}
                    highlight={(code) => code}
                    padding={12}
                    className="min-h-[240px] rounded-2xl border border-border bg-secondary/20 font-mono text-xs text-foreground"
                    style={{ fontFamily: "ui-monospace, SFMono-Regular, SFMono, Menlo, monospace", whiteSpace: "pre-wrap" }}
                  />
                  <p className="mt-2 text-xs text-muted-foreground">
                    Webhooks usually need `url`, `method`, and optional `headers`. Local scripts usually need `script_path`, `working_directory`, and optional `timeout_seconds`.
                  </p>
                </div>
              </div>

              <div className="flex justify-end gap-2 border-t border-border pt-4">
                <button
                  type="button"
                  onClick={() => setToolModalOpen(false)}
                  className="rounded-xl border border-border bg-secondary px-4 py-2 text-sm text-secondary-foreground transition-colors hover:text-foreground"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={saveCustomToolDraft}
                  className="rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground transition-opacity hover:opacity-90"
                >
                  Save Tool
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
