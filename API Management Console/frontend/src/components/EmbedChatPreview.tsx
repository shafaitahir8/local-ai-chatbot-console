import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Bot as BotIcon, ChevronDown, ChevronUp, ExternalLink, Send, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import {
  ChatProgressStage,
  executeAdminToolAction,
  fetchBotStatus,
  sendAdminPreviewMessage,
  sendMessage,
} from "@/lib/api";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { PendingToolAction, QuickChip, QuickChipDefinition } from "@/types/bot";

interface CitationItem {
  label: string;
  scope?: string | null;
  snippet?: string | null;
  score?: number | null;
}

interface PreviewMessage {
  id: string;
  role: "user" | "bot";
  text: string;
  pending?: boolean;
  contextScope?: string | null;
  contextSources?: string[];
  citations?: CitationItem[];
  quickChips?: QuickChip[];
}

interface EmbedChatPreviewProps {
  botName: string;
  botId: string;
  accentColor?: string;
  mode?: "public" | "admin";
  welcomeMessage?: string;
  initialQuickChips?: QuickChipDefinition[];
}

const fallbackChipDefinitions: QuickChipDefinition[] = [
  { label: "Audit a HubSpot module", action_type: "suggested_prompt", value: "Audit a HubSpot module", style: "outline", visibility_mode: "on_start", trigger_keywords: [], ai_instruction: "", sentiment_filter: "any", priority: 0 },
  { label: "Fix responsive CSS", action_type: "suggested_prompt", value: "Fix responsive CSS", style: "outline", visibility_mode: "on_start", trigger_keywords: [], ai_instruction: "", sentiment_filter: "any", priority: 0 },
  { label: "Write HubL", action_type: "suggested_prompt", value: "Write HubL", style: "outline", visibility_mode: "on_start", trigger_keywords: [], ai_instruction: "", sentiment_filter: "any", priority: 0 },
  { label: "Summarize this page", action_type: "suggested_prompt", value: "Summarize this page", style: "outline", visibility_mode: "on_start", trigger_keywords: [], ai_instruction: "", sentiment_filter: "any", priority: 0 },
];

const progressSteps: Array<{ id: ChatProgressStage | "validate_request"; label: string }> = [
  { id: "prepare_request", label: "Validating request" },
  { id: "searching_knowledge_base", label: "Searching knowledge base" },
  { id: "checking_module_references", label: "Checking module references" },
  { id: "awaiting_response", label: "Drafting response" },
  { id: "streaming_answer", label: "Streaming answer" },
];

function currentStepIndex(stage: ChatProgressStage | null) {
  switch (stage) {
    case "prepare_request":
    case "opening_session":
    case "retrying_request":
      return 0;
    case "searching_knowledge_base":
      return 1;
    case "checking_module_references":
    case "warming_provider":
      return 2;
    case "awaiting_response":
      return 3;
    case "streaming_answer":
    case "completed":
      return 4;
    default:
      return 0;
  }
}

function normalizeQuickChipCollection(
  chips: QuickChipDefinition[] | QuickChip[] | undefined,
  prefix: string
): QuickChip[] {
  return (chips || []).map((chip, index) => {
    const legacyVisibility = (chip as QuickChip & { visibility?: string }).visibility;
    const visibilityMode = chip.visibility_mode
      || (legacyVisibility === "on_start_only"
        ? "on_start"
        : legacyVisibility === "conditional"
          ? "keywords"
          : "always");
    const normalizedChip = {
      id: chip.id || `${prefix}-${index + 1}`,
      label: chip.label,
      action_type: chip.action_type,
      value: chip.value,
      style: chip.style,
      visibility_mode: visibilityMode,
      trigger_keywords: chip.trigger_keywords || [],
      ai_instruction: chip.ai_instruction || "",
      sentiment_filter: chip.sentiment_filter || "any",
      priority: chip.priority || 0,
      payload: chip.payload || {},
    };
    if (!normalizedChip.label || !normalizedChip.value) {
      return null;
    }
    if (normalizedChip.visibility_mode === "keywords" && normalizedChip.trigger_keywords.length === 0 && !normalizedChip.ai_instruction.trim()) {
      return null;
    }
    if (normalizedChip.visibility_mode === "ai_only" && !normalizedChip.ai_instruction.trim()) {
      return null;
    }
    return normalizedChip;
  }).filter((chip): chip is QuickChip => chip !== null);
}

function buildWelcomeText(botName: string, explicitWelcome?: string) {
  return explicitWelcome || `Hi! I'm ${botName}. I can help with HubSpot CMS, HubL, modules, content operations, and implementation guidance.`;
}

function buildWelcomeMessage(
  botName: string,
  welcomeText?: string,
  startupChips?: QuickChipDefinition[] | QuickChip[]
): PreviewMessage {
  const chips = normalizeQuickChipCollection(
    startupChips && startupChips.length > 0 ? startupChips : fallbackChipDefinitions,
    "welcome-chip"
  );
  return {
    id: "welcome",
    role: "bot",
    text: buildWelcomeText(botName, welcomeText),
    contextScope: "HubSpot CMS",
    contextSources: ["HubSpot KB", "Bot knowledge base"],
    citations: [],
    quickChips: chips,
  };
}

function buildAssistantMessage(
  id: string,
  response: {
    response: string;
    context_scope?: string | null;
    context_sources?: string[];
    citations?: CitationItem[];
    quick_chips?: QuickChip[];
  }
): PreviewMessage {
  return {
    id,
    role: "bot",
    text: response.response,
    pending: false,
    contextScope: response.context_scope || undefined,
    contextSources: response.context_sources || [],
    citations: response.citations || [],
    quickChips: normalizeQuickChipCollection(response.quick_chips, `${id}-chip`),
  };
}

function chipButtonClasses(style: QuickChip["style"]) {
  switch (style) {
    case "primary":
      return "border-primary/35 bg-primary/10 text-primary hover:border-primary/50 hover:bg-primary/15";
    case "ghost":
      return "border-transparent bg-secondary/55 text-foreground hover:border-border hover:bg-secondary";
    default:
      return "border-border bg-background text-muted-foreground hover:border-primary/30 hover:text-foreground";
  }
}

function buildChipPromptPayload(chip: QuickChip) {
  if (chip.action_type !== "suggested_prompt") {
    return chip.label;
  }
  const hiddenValue = (chip.value || chip.label).trim();
  return [
    "Respond as a chatbot in 1-2 short sentences.",
    "Do not write an email, letter, greeting, sign-off, or placeholders.",
    "Give the user the direct answer only.",
    hiddenValue,
  ].join("\n");
}

export function EmbedChatPreview({
  botName,
  botId,
  accentColor = "#00d4aa",
  mode = "public",
  welcomeMessage,
  initialQuickChips,
}: EmbedChatPreviewProps) {
  const initialStartupChips = useMemo(
    () => normalizeQuickChipCollection(initialQuickChips, "initial-prop-chip"),
    [initialQuickChips]
  );

  const [messages, setMessages] = useState<PreviewMessage[]>([
    buildWelcomeMessage(botName, welcomeMessage, initialStartupChips),
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState<ChatProgressStage | null>(null);
  const [loadingText, setLoadingText] = useState("Ready.");
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const [expandedCitations, setExpandedCitations] = useState<Record<string, boolean>>({});
  const [pendingToolActions, setPendingToolActions] = useState<PendingToolAction[]>([]);
  const [approvingToolId, setApprovingToolId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const activeStep = useMemo(() => currentStepIndex(loadingStage), [loadingStage]);

  useEffect(() => {
    setMessages([buildWelcomeMessage(botName, welcomeMessage, initialStartupChips)]);
    setInput("");
    setLoading(false);
    setLoadingStage(null);
    setLoadingText("Ready.");
    setSessionId(undefined);
    setExpandedCitations({});
    setPendingToolActions([]);
    setApprovingToolId(null);
  }, [botId, botName, welcomeMessage, initialStartupChips]);

  useEffect(() => {
    if (mode !== "public" || welcomeMessage || initialStartupChips.length > 0) {
      return;
    }

    let cancelled = false;
    fetchBotStatus(botId)
      .then((status) => {
        if (cancelled) return;
        const startupChips = normalizeQuickChipCollection(status.quick_chip_settings.initial_chips, "status-chip");
        setMessages([
          buildWelcomeMessage(
            botName,
            status.startup_welcome_message || status.welcome_message || buildWelcomeText(botName),
            startupChips
          ),
        ]);
      })
      .catch(() => {
        if (cancelled) return;
        setMessages([buildWelcomeMessage(botName, welcomeMessage, initialStartupChips)]);
      });

    return () => {
      cancelled = true;
    };
  }, [mode, botId, botName, welcomeMessage, initialStartupChips]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading, loadingStage, pendingToolActions]);

  const toggleCitations = (messageId: string) => {
    setExpandedCitations((prev) => ({ ...prev, [messageId]: !prev[messageId] }));
  };

  const appendAssistantResponse = (
    response: {
      response: string;
      session_id?: string;
      context_scope?: string | null;
      context_sources?: string[];
      citations?: CitationItem[];
      quick_chips?: QuickChip[];
      pending_tool_actions?: PendingToolAction[];
    },
    messageId?: string
  ) => {
    const nextId = messageId || `assistant-${Date.now()}`;
    const assistantMessage = buildAssistantMessage(nextId, response);
    setMessages((prev) => {
      if (!messageId) {
        return [...prev, assistantMessage];
      }
      return prev.map((message) => (message.id === messageId ? assistantMessage : message));
    });
    if (response.session_id) {
      setSessionId(response.session_id);
    }
    setPendingToolActions(response.pending_tool_actions || []);
  };

  const send = async (seedMessage?: string, visibleUserMessage?: string) => {
    const outboundMessage = (seedMessage ?? input).trim();
    const visibleMessage = (visibleUserMessage ?? seedMessage ?? input).trim();
    if (!outboundMessage || !visibleMessage || loading) return;

    const assistantId = `assistant-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: `user-${Date.now()}`, role: "user", text: visibleMessage },
      { id: assistantId, role: "bot", text: "", pending: true, citations: [], quickChips: [] },
    ]);
    setInput("");
    setLoading(true);
    setPendingToolActions([]);
    setLoadingStage(sessionId ? "prepare_request" : "opening_session");
    setLoadingText(sessionId ? "Validating request..." : "Opening a new chat session...");

    try {
      const sendFn = mode === "admin" ? sendAdminPreviewMessage : sendMessage;
      const response = await sendFn(botId, outboundMessage, sessionId, {
        maxRetries: 2,
        onProgress: (update) => {
          setLoadingStage(update.stage);
          setLoadingText(update.message);
        },
        onChunk: (chunk) => {
          if (!chunk) return;
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    pending: false,
                    text: `${msg.text}${chunk}`,
                  }
                : msg
            )
          );
        },
      });

      appendAssistantResponse(response, assistantId);
      setLoadingStage("completed");
      setLoadingText("Response received.");
    } catch (error) {
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantId
            ? {
                ...msg,
                pending: false,
                text: "Sorry, I hit an error while processing that request. Try refining the ask or using one of the quick chips below.",
                contextScope: "HubSpot CMS",
                contextSources: [],
                citations: [],
                quickChips: [],
              }
            : msg
        )
      );
      setLoadingText(error instanceof Error ? error.message : "Request failed.");
    } finally {
      setLoading(false);
    }
  };

  const approvePendingAction = async (action: PendingToolAction) => {
    if (mode !== "admin" || approvingToolId) return;
    setApprovingToolId(action.id);
    try {
      const result = await executeAdminToolAction(botId, action.tool_id, action.payload || {}, sessionId);
      setPendingToolActions((prev) => prev.filter((item) => item.id !== action.id));
      appendAssistantResponse(result.follow_up);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          id: `tool-error-${Date.now()}`,
          role: "bot",
          text: error instanceof Error ? error.message : "Tool action failed.",
          citations: [],
          quickChips: [],
        },
      ]);
    } finally {
      setApprovingToolId(null);
    }
  };

  const handleChipClick = (chip: QuickChip) => {
    if (loading) return;
    if (chip.action_type === "suggested_prompt") {
      void send(buildChipPromptPayload(chip), chip.label);
      return;
    }
    void send(chip.label, chip.label);
  };

  return (
    <div className="w-full max-w-none overflow-hidden rounded-xl border border-border bg-card shadow-lg">
      <div className="flex items-center gap-3 border-b border-border bg-secondary/40 px-5 py-4" style={{ borderBottomColor: `${accentColor}33` }}>
        <div className="flex h-9 w-9 items-center justify-center rounded-full" style={{ background: `${accentColor}20` }}>
          <BotIcon className="h-4 w-4" style={{ color: accentColor }} />
        </div>
        <div>
          <span className="text-sm font-semibold text-foreground">{botName}</span>
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            {mode === "admin" ? "Admin Tool Preview" : "Premium HubSpot Specialist"}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-primary" />
          <div className="h-2 w-2 rounded-full animate-pulse-glow" style={{ background: accentColor }} />
        </div>
      </div>

      <div className="h-[460px] space-y-4 overflow-y-auto p-4">
        {messages.map((msg) => {
          const citationsOpen = !!expandedCitations[msg.id];
          const showUsingBar = msg.role === "bot" && ((msg.contextSources?.length || 0) > 0 || !!msg.contextScope);
          const showSteps = msg.role === "bot" && msg.pending;
          const showChips = msg.role === "bot" && !msg.pending && (msg.quickChips?.length || 0) > 0;

          return (
            <motion.div
              key={msg.id}
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              className={`flex gap-2 ${msg.role === "user" ? "justify-end" : ""}`}
            >
              {msg.role === "bot" && (
                <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full" style={{ background: `${accentColor}20` }}>
                  <BotIcon className="h-2.5 w-2.5" style={{ color: accentColor }} />
                </div>
              )}

              <div className={`max-w-[85%] ${msg.role === "user" ? "" : "min-w-[260px]"}`}>
                {showSteps && (
                  <div className="mb-2 rounded-xl border border-border bg-background/70 px-3 py-2">
                    <div className="mb-2 flex items-center justify-between">
                      <span className="font-mono text-[10px] uppercase tracking-wide text-primary">Live request steps</span>
                      <span className="font-mono text-[10px] text-muted-foreground">{loadingText}</span>
                    </div>
                    <div className="grid gap-1 sm:grid-cols-5">
                      {progressSteps.map((step, index) => {
                        const isComplete = index < activeStep;
                        const isActive = index === activeStep;
                        return (
                          <div
                            key={step.label}
                            className={`rounded-lg border px-2 py-1.5 ${
                              isActive
                                ? "border-primary/40 bg-primary/10"
                                : isComplete
                                  ? "border-success/30 bg-success/10"
                                  : "border-border bg-secondary/30"
                            }`}
                          >
                            <div className="flex items-center gap-1.5">
                              <div className={`h-2 w-2 rounded-full ${isActive ? "bg-primary" : isComplete ? "bg-success" : "bg-muted-foreground/50"}`} />
                              <span className={`font-mono text-[10px] ${isActive ? "text-primary" : isComplete ? "text-success" : "text-muted-foreground"}`}>
                                {step.label}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {showUsingBar && (
                  <div className="mb-2 rounded-xl border border-border bg-background/70 px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Using</span>
                      {msg.contextScope && (
                        <span className="rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 font-mono text-[10px] text-primary">
                          {msg.contextScope}
                        </span>
                      )}
                      {(msg.contextSources || []).map((source) => (
                        <span key={`${msg.id}-${source}`} className="rounded-full border border-border bg-secondary/45 px-2 py-0.5 font-mono text-[10px] text-foreground">
                          {source}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <div className={`rounded-xl px-3 py-2 text-sm ${msg.role === "user" ? "bg-primary/10 text-foreground" : "bg-secondary text-foreground"}`}>
                  {msg.pending && !msg.text ? (
                    <div className="space-y-2">
                      <div className="h-2.5 w-full animate-pulse rounded bg-muted-foreground/20" />
                      <div className="h-2.5 w-5/6 animate-pulse rounded bg-muted-foreground/20" />
                      <div className="h-2.5 w-4/6 animate-pulse rounded bg-muted-foreground/20" />
                    </div>
                  ) : (
                    <MarkdownMessage content={msg.text} />
                  )}
                </div>

                {showChips && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {msg.quickChips?.map((chip) => (
                      <button
                        key={chip.id}
                        onClick={() => handleChipClick(chip)}
                        disabled={loading}
                        className={`inline-flex max-w-full items-center gap-1 rounded-xl border px-3 py-1.5 text-left font-mono text-[10px] uppercase tracking-wide transition-colors disabled:opacity-50 ${chipButtonClasses(chip.style)}`}
                      >
                        <span className="truncate">{chip.label}</span>
                        {chip.action_type === "link" && <ExternalLink className="h-3 w-3 shrink-0" />}
                        {chip.action_type === "tool_call" && <Wrench className="h-3 w-3 shrink-0" />}
                      </button>
                    ))}
                  </div>
                )}

                {msg.role === "bot" && (msg.citations?.length || 0) > 0 && (
                  <div className="mt-2">
                    <button
                      onClick={() => toggleCitations(msg.id)}
                      className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {citationsOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                      References ({msg.citations?.length || 0})
                    </button>
                    {citationsOpen && (
                      <div className="mt-2 space-y-2 rounded-xl border border-border bg-background/70 p-3">
                        {(msg.citations || []).map((citation, index) => (
                          <div key={`${msg.id}-citation-${index}`} className="rounded-lg border border-border bg-secondary/35 p-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="font-mono text-[10px] text-primary">{citation.label}</span>
                              {citation.scope && (
                                <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
                                  {citation.scope}
                                </span>
                              )}
                              {typeof citation.score === "number" && (
                                <span className="font-mono text-[10px] text-muted-foreground">score {citation.score.toFixed(2)}</span>
                              )}
                            </div>
                            {citation.snippet && <p className="mt-2 text-[11px] leading-relaxed text-foreground/90">{citation.snippet}</p>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </motion.div>
          );
        })}
        {mode === "admin" && pendingToolActions.length > 0 && (
          <div className="space-y-2">
            {pendingToolActions.map((action) => (
              <div key={action.id} className="rounded-xl border border-warning/30 bg-warning/5 p-3">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 flex h-7 w-7 items-center justify-center rounded-full bg-warning/10 text-warning">
                    <ShieldCheck className="h-3.5 w-3.5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-foreground">{action.label}</span>
                      <span className="rounded-full border border-warning/30 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-warning">
                        Approval required
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">{action.summary}</p>
                    {action.execution_type === "local_script" && (
                      <div className="mt-3 flex items-start gap-2 rounded-xl border border-destructive/25 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        <span>This local script runs with the desktop user&apos;s permissions. Review the script path and working directory before approving.</span>
                      </div>
                    )}
                    <button
                      onClick={() => approvePendingAction(action)}
                      disabled={approvingToolId === action.id}
                      className="mt-3 inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
                    >
                      {approvingToolId === action.id ? <Sparkles className="h-3.5 w-3.5 animate-pulse" /> : <Wrench className="h-3.5 w-3.5" />}
                      Approve and Run
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="border-t border-border bg-secondary/20 p-4">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Ask about HubSpot modules, HubL, CSS, CRM mappings, or delivery scope..."
            disabled={loading}
            className="flex-1 rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground transition-colors focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10 disabled:opacity-50"
          />
          <button
            onClick={() => send()}
            disabled={loading || !input.trim()}
            className="rounded-xl px-4 py-2.5 text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ background: accentColor }}
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
