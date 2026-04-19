import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Bot } from "@/types/bot";
import {
  updateBot,
  uploadDocument,
  rebuildEmbeddings,
  scrapeWebsite,
  fetchScraperJobStatus,
  fetchKnowledgeChunks,
  fetchKnowledgeChunkSource,
  deleteKnowledgeChunk,
  updateKnowledgeChunk,
  getSystemStatus,
  expireBot,
  recoverBot,
  getBotModelFilesFolder,
  openBotModelFilesFolder,
  fetchAdminToolCatalog,
} from "@/lib/api";
import { EmbedChatPreview } from "@/components/EmbedChatPreview";
import { BotToolingPanel } from "@/components/BotToolingPanel";
import { toast } from "sonner";
import { ToolCatalogItem } from "@/types/bot";
import {
  ArrowLeft,
  Save,
  Copy,
  Globe,
  Upload,
  ToggleLeft,
  ToggleRight,
  RefreshCw,
  MessageSquare,
  Zap,
  Clock,
  Brain,
  Thermometer,
  Calendar,
  Loader2,
  FolderOpen,
  Archive,
  RotateCcw,
  ShieldCheck,
  Link2,
  CheckCircle2,
  Rocket,
  Pencil,
  Trash2,
  X,
} from "lucide-react";

type DetailTab = "overview" | "knowledge" | "preview" | "technical";

interface BotDetailViewProps {
  bot: Bot;
  onBack: () => void;
  onSave: (bot: Bot) => void;
  onClone: (bot: Bot) => void;
  shellStatus?: {
    encryption_enabled?: boolean;
    public_tunnel_ready?: boolean;
    tunnel_stable?: boolean;
    local_llm?: string;
    faiss?: string;
  };
}

function formatKnowledgeState(chunkCount: number) {
  if (chunkCount >= 10) return "Knowledge-rich";
  if (chunkCount > 0) return "Targeted context";
  return "No indexed context";
}

export function BotDetailView({ bot, onBack, onSave, onClone, shellStatus }: BotDetailViewProps) {
  const [editBot, setEditBot] = useState<Bot>({ ...bot });
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");
  const [scrapeUrl, setScrapeUrl] = useState("");
  const [scrapeDepth, setScrapeDepth] = useState(2);
  const [scrapeLimit, setScrapeLimit] = useState(20);
  const [scrapeFocus, setScrapeFocus] = useState("");
  const [scrapeSitemapOnly, setScrapeSitemapOnly] = useState(false);
  const [activeScrapeJobId, setActiveScrapeJobId] = useState<string | null>(null);
  const [showScrapeInput, setShowScrapeInput] = useState(false);
  const [knowledgeSearch, setKnowledgeSearch] = useState("");
  const [knowledgePage, setKnowledgePage] = useState(1);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [sourceView, setSourceView] = useState<"cleaned" | "raw">("cleaned");
  const [editingChunkId, setEditingChunkId] = useState<string | null>(null);
  const [editingChunkContent, setEditingChunkContent] = useState("");
  const [stickyPublicBaseUrl, setStickyPublicBaseUrl] = useState<string>("");
  const queryClient = useQueryClient();

  useEffect(() => {
    setEditBot({ ...bot });
    setActiveTab("overview");
  }, [bot]);

  const { data: systemStatus } = useQuery({
    queryKey: ["system-status"],
    queryFn: getSystemStatus,
    refetchInterval: 30000,
  });

  const { data: knowledgeData, isLoading: knowledgeLoading, error: knowledgeError } = useQuery({
    queryKey: ["knowledge-chunks", bot.id, knowledgePage, knowledgeSearch],
    queryFn: () => fetchKnowledgeChunks(bot.id, knowledgePage, 8, knowledgeSearch),
    enabled: !!bot.id,
  });

  const { data: selectedChunkSource, isLoading: selectedChunkSourceLoading, error: selectedChunkSourceError } = useQuery({
    queryKey: ["knowledge-chunk-source", bot.id, selectedChunkId],
    queryFn: () => fetchKnowledgeChunkSource(bot.id, selectedChunkId!),
    enabled: !!bot.id && !!selectedChunkId,
  });

  const { data: modelFolder } = useQuery({
    queryKey: ["bot-model-folder", bot.id],
    queryFn: () => getBotModelFilesFolder(bot.id),
    enabled: !!bot.id,
  });

  const { data: toolCatalog = [] } = useQuery<ToolCatalogItem[]>({
    queryKey: ["admin-tool-catalog"],
    queryFn: fetchAdminToolCatalog,
  });

  const { data: activeScrapeJob } = useQuery({
    queryKey: ["scraper-job", activeScrapeJobId],
    queryFn: () => fetchScraperJobStatus(activeScrapeJobId!),
    enabled: !!activeScrapeJobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["completed", "failed", "review"].includes(status) ? false : 5000;
    },
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      updateBot(bot.id, {
        name: editBot.name,
        system_prompt: editBot.system_prompt,
        temperature: editBot.temperature,
        memory_enabled: editBot.memory_enabled,
        message_limit: editBot.message_limit,
        is_active: editBot.status === "active",
        expiration_date: editBot.status === "expired" ? new Date().toISOString() : null,
        accent_color: editBot.accent_color,
        tools_enabled: editBot.tools_enabled,
        allowed_tools: editBot.allowed_tools,
        tool_settings: editBot.tool_settings,
        quick_chip_settings: editBot.quick_chip_settings,
      }),
    onSuccess: (updated) => {
      toast.success("Bot saved");
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      onSave(updated);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Save failed");
    },
  });

  const expireMutation = useMutation({
    mutationFn: () => expireBot(bot.id),
    onSuccess: (updated) => {
      toast.success("Bot expired");
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      setEditBot(updated);
      onSave(updated);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Expire failed");
    },
  });

  const recoverMutation = useMutation({
    mutationFn: () => recoverBot(bot.id),
    onSuccess: (updated) => {
      toast.success("Bot recovered to active");
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      setEditBot(updated);
      onSave(updated);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Recover failed");
    },
  });

  const openFolderMutation = useMutation({
    mutationFn: () => openBotModelFilesFolder(bot.id),
    onSuccess: () => toast.success("Opened model files folder"),
    onError: (error) => toast.error(error instanceof Error ? error.message : "Could not open folder"),
  });

  const rebuildMutation = useMutation({
    mutationFn: () => rebuildEmbeddings(bot.id),
    onSuccess: () => {
      toast.success("Embeddings rebuilt");
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunks", bot.id] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Rebuild failed"),
  });

  const scrapeMutation = useMutation({
    mutationFn: (url: string) =>
      scrapeWebsite(bot.id, url, {
        depth: scrapeDepth,
        limit: scrapeLimit,
        focus: scrapeFocus,
        sitemap_only: scrapeSitemapOnly,
      }),
    onSuccess: (data) => {
      toast.success(`Scraper job started (ID: ${data.id})`);
      setActiveScrapeJobId(data.id);
      setShowScrapeInput(false);
      setScrapeUrl("");
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunks", bot.id] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Scrape failed"),
  });

  const deleteChunkMutation = useMutation({
    mutationFn: (chunkId: string) => deleteKnowledgeChunk(bot.id, chunkId),
    onSuccess: () => {
      toast.success("Chunk deleted");
      if (selectedChunkId && selectedChunkId === editingChunkId) {
        setSelectedChunkId(null);
      }
      setEditingChunkId(null);
      setEditingChunkContent("");
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunks", bot.id] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunk-source", bot.id] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Delete failed"),
  });

  const updateChunkMutation = useMutation({
    mutationFn: ({ chunkId, content }: { chunkId: string; content: string }) => updateKnowledgeChunk(bot.id, chunkId, content),
    onSuccess: (updatedChunk) => {
      toast.success("Chunk updated");
      setEditingChunkId(null);
      setEditingChunkContent("");
      if (selectedChunkId === updatedChunk.id) {
        queryClient.invalidateQueries({ queryKey: ["knowledge-chunk-source", bot.id, updatedChunk.id] });
      }
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunks", bot.id] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Update failed"),
  });

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await uploadDocument(bot.id, file);
      toast.success(`Uploaded ${file.name}`);
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunks", bot.id] });
      queryClient.invalidateQueries({ queryKey: ["bot-model-folder", bot.id] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Upload failed");
    }
  };

  const publicBaseUrl = systemStatus?.public_base_url?.replace(/\/$/, "");
  useEffect(() => {
    if (publicBaseUrl) {
      setStickyPublicBaseUrl(publicBaseUrl);
    }
  }, [publicBaseUrl]);

  const effectivePublicBaseUrl = (publicBaseUrl || stickyPublicBaseUrl || "").replace(/\/$/, "");
  const cloudflareStable = systemStatus?.cloudflare_stable ?? false;
  const tunnelTokenRequired = systemStatus?.tunnel_token_required ?? false;
  const tunnelToken = (systemStatus?.tunnel_token || "").trim();
  const withOptionalToken = (url: string) => {
    if (!tunnelToken) return url;
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}token=${encodeURIComponent(tunnelToken)}`;
  };

  const localApiEndpoint = `${window.location.origin}/api/bot/${bot.id}/chat`;
  const localEmbedUrl = `${window.location.origin}/embed/${bot.id}`;
  const publicChatEndpointBase = effectivePublicBaseUrl ? `${effectivePublicBaseUrl}/api/bot/${bot.id}/chat` : null;
  const publicStatusEndpointBase = effectivePublicBaseUrl ? `${effectivePublicBaseUrl}/api/bot/${bot.id}/status` : null;
  const publicEmbedUrlBase = effectivePublicBaseUrl ? `${effectivePublicBaseUrl}/embed/${bot.id}` : null;
  const cloudflareEndpoint = publicChatEndpointBase ? withOptionalToken(publicChatEndpointBase) : null;
  const apiEndpoint = cloudflareEndpoint || localApiEndpoint;
  const embedUrl = publicEmbedUrlBase ? withOptionalToken(publicEmbedUrlBase) : localEmbedUrl;
  const hubspotModuleSnippet = `# HubSpot module field values
api_endpoint="${apiEndpoint}"
bot_name="${(editBot.name || bot.name || "AI Assistant").replace(/"/g, '\\"')}"
accent_color="${editBot.accent_color || bot.accent_color || "#007bff"}"

# Optional public endpoints
status_endpoint="${publicStatusEndpointBase ? withOptionalToken(publicStatusEndpointBase) : `${window.location.origin}/api/bot/${bot.id}/status`}"
embed_url="${embedUrl}"`;

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text).then(() => toast.success(`${label} copied to clipboard`));
  };

  const beginChunkEdit = (chunkId: string, content: string) => {
    setEditingChunkId(chunkId);
    setEditingChunkContent(content);
  };

  useEffect(() => {
    if (!activeScrapeJob) {
      return;
    }
    if (["completed", "review"].includes(activeScrapeJob.status)) {
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunks", bot.id] });
    }
  }, [activeScrapeJob, bot.id, queryClient]);

  const cancelChunkEdit = () => {
    setEditingChunkId(null);
    setEditingChunkContent("");
  };

  const trustBadges = [
    {
      label: "Encrypted",
      value: shellStatus?.encryption_enabled ? "Protected" : "Review",
      ok: !!shellStatus?.encryption_enabled,
      icon: ShieldCheck,
    },
    {
      label: "Indexed",
      value: (knowledgeData?.pagination.total_items || 0) > 0 && shellStatus?.faiss === "Loaded" ? "Ready" : "Pending",
      ok: (knowledgeData?.pagination.total_items || 0) > 0 && shellStatus?.faiss === "Loaded",
      icon: Brain,
    },
    {
      label: "Hot",
      value: shellStatus?.local_llm === "Running" ? "Ready" : "Warming",
      ok: shellStatus?.local_llm === "Running",
      icon: Zap,
    },
    {
      label: "Connected",
      value: shellStatus?.public_tunnel_ready ? (shellStatus?.tunnel_stable ? "Stable" : "Live") : "Local only",
      ok: !!shellStatus?.public_tunnel_ready,
      icon: Globe,
    },
  ];

  const overviewStats = useMemo(
    () => [
      { icon: MessageSquare, label: "Messages", value: (bot.messages_used || 0).toLocaleString(), color: "text-primary" },
      { icon: Zap, label: "Tokens", value: `${((bot.token_usage || 0) / 1000).toFixed(1)}k`, color: "text-warning" },
      { icon: Clock, label: "Avg Latency", value: `${(bot.avg_latency || 0).toFixed(1)}s`, color: "text-accent" },
      { icon: Calendar, label: "Created", value: new Date(bot.created_at).toLocaleDateString(), color: "text-muted-foreground" },
    ],
    [bot]
  );

  const scrapeTelemetry = (activeScrapeJob?.telemetry || {}) as Record<string, unknown>;
  const scrapeSummary = (activeScrapeJob?.summary || {}) as Record<string, unknown>;
  const scrapePriorityCounts = (scrapeSummary.priority_counts || {}) as Record<string, number>;

  const tabs: Array<{ id: DetailTab; label: string }> = [
    { id: "overview", label: "Overview" },
    { id: "knowledge", label: "Knowledge" },
    { id: "preview", label: "Preview" },
    { id: "technical", label: "Technical" },
  ];

  return (
    <div>
      <button onClick={onBack} className="mb-4 flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to Operations
      </button>

      <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">{editBot.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <p className="font-mono text-xs text-muted-foreground">{`${bot.id} · ${bot.provider_type}`}</p>
            <button
              onClick={() => copyToClipboard(bot.id, "Bot ID")}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary/50 px-2 py-1 font-mono text-[10px] text-muted-foreground transition-colors hover:text-foreground"
            >
              <Copy className="h-3 w-3" />
              Copy ID
            </button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {trustBadges.map((item) => (
              <div key={item.label} className="rounded-full border border-border bg-secondary/45 px-3 py-1.5">
                <div className="flex items-center gap-1.5">
                  {item.ok ? <CheckCircle2 className="h-3.5 w-3.5 text-success" /> : <item.icon className="h-3.5 w-3.5 text-warning" />}
                  <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">{item.label}</span>
                  <span className={`font-mono text-[10px] ${item.ok ? "text-success" : "text-warning"}`}>{item.value}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => onClone(bot)}
            className="flex items-center gap-1.5 rounded-xl border border-border bg-secondary px-3 py-1.5 text-sm text-secondary-foreground transition-colors hover:text-foreground"
          >
            <Copy className="h-3 w-3" /> Clone
          </button>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {saveMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            Save
          </button>
        </div>
      </div>

      <div className="mb-5 flex flex-wrap gap-2">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`rounded-full border px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide transition-all ${
              activeTab === tab.id
                ? "border-primary/30 bg-primary/10 text-primary"
                : "border-border bg-secondary text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "overview" && (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="rounded-[1rem] border border-border bg-card p-5 surface-shadow">
            <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Client Summary</p>
            <h2 className="mt-2 text-xl font-semibold text-foreground">HubSpot specialist ready for guided delivery work</h2>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              This assistant is configured for buyer-safe HubSpot support. It can answer from indexed knowledge, provide live preview
              responses, and publish through a private local or tunneled endpoint.
            </p>

            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border border-border bg-secondary/35 p-4">
                <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Knowledge posture</p>
                <p className="mt-2 text-sm text-foreground">{formatKnowledgeState(knowledgeData?.pagination.total_items || 0)}</p>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                  {knowledgeData?.pagination.total_items || 0} indexed chunks available for retrieval
                </p>
              </div>
              <div className="rounded-2xl border border-border bg-secondary/35 p-4">
                <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Delivery endpoint</p>
                <p className="mt-2 text-sm text-foreground">{cloudflareEndpoint ? "Public and embed-ready" : "Local preview only"}</p>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">{cloudflareEndpoint || "Public tunnel not ready yet"}</p>
              </div>
            </div>

            <div className="mt-5 rounded-2xl border border-border bg-secondary/25 p-4">
              <div className="mb-3 flex items-center gap-2">
                <Link2 className="h-4 w-4 text-primary" />
                <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Shareable Endpoints</p>
              </div>
              <div className="space-y-3">
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-mono text-[10px] text-muted-foreground">Bot ID</span>
                    <button onClick={() => copyToClipboard(bot.id, "Bot ID")} className="text-muted-foreground hover:text-foreground">
                      <Copy className="h-3 w-3" />
                    </button>
                  </div>
                  <code className="block break-all rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[10px] text-primary">{bot.id}</code>
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-mono text-[10px] text-muted-foreground">API Endpoint</span>
                    <button onClick={() => copyToClipboard(apiEndpoint, "API endpoint")} className="text-muted-foreground hover:text-foreground">
                      <Copy className="h-3 w-3" />
                    </button>
                  </div>
                  <code className="block break-all rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[10px] text-primary">{apiEndpoint}</code>
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-mono text-[10px] text-muted-foreground">HubSpot Module Snippet</span>
                    <button onClick={() => copyToClipboard(hubspotModuleSnippet, "HubSpot module snippet")} className="text-muted-foreground hover:text-foreground">
                      <Copy className="h-3 w-3" />
                    </button>
                  </div>
                  <pre className="block overflow-x-auto whitespace-pre-wrap rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[10px] text-primary">{hubspotModuleSnippet}</pre>
                </div>
              </div>
            </div>
          </motion.div>

          <div className="space-y-5">
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="rounded-[1rem] border border-border bg-card p-4 surface-shadow">
              <label className="block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Runtime Statistics</label>
              <div className="mt-3 space-y-3">
                {overviewStats.map((stat) => (
                  <div key={stat.label} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <stat.icon className={`h-3.5 w-3.5 ${stat.color}`} />
                      <span className="font-mono text-xs text-muted-foreground">{stat.label}</span>
                    </div>
                    <span className="font-mono text-xs text-foreground">{stat.value}</span>
                  </div>
                ))}
              </div>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="rounded-[1rem] border border-border bg-card p-4 surface-shadow">
              <label className="block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Operational Controls</label>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  onClick={() => expireMutation.mutate()}
                  disabled={expireMutation.isPending || editBot.status === "expired"}
                  className="flex items-center gap-1.5 rounded border border-destructive/40 bg-destructive/10 px-2 py-1 font-mono text-[10px] text-destructive disabled:opacity-50"
                >
                  {expireMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Archive className="h-3 w-3" />}
                  Expire Bot
                </button>
                <button
                  onClick={() => recoverMutation.mutate()}
                  disabled={recoverMutation.isPending || editBot.status === "active"}
                  className="flex items-center gap-1.5 rounded border border-success/30 bg-success/10 px-2 py-1 font-mono text-[10px] text-success disabled:opacity-50"
                >
                  {recoverMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                  Recover To Active
                </button>
                <button
                  onClick={() => rebuildMutation.mutate()}
                  disabled={rebuildMutation.isPending}
                  className="flex items-center gap-1.5 rounded border border-primary/30 bg-primary/10 px-2 py-1 font-mono text-[10px] text-primary disabled:opacity-50"
                >
                  {rebuildMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                  Rebuild Index
                </button>
              </div>
            </motion.div>
          </div>
        </div>
      )}

      {activeTab === "knowledge" && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="rounded-[1rem] border border-border bg-card p-4 surface-shadow">
          <div className="mb-3 flex items-center justify-between">
            <label className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Knowledge Base</label>
            <div className="flex gap-2">
              <button
                onClick={() => setShowScrapeInput(!showScrapeInput)}
                className="flex items-center gap-1.5 rounded border border-border bg-secondary px-2 py-1 font-mono text-[10px] text-secondary-foreground transition-colors hover:text-foreground"
              >
                <Globe className="h-3 w-3" /> Scrape Website
              </button>
              <label className="flex cursor-pointer items-center gap-1.5 rounded border border-border bg-secondary px-2 py-1 font-mono text-[10px] text-secondary-foreground transition-colors hover:text-foreground">
                <Upload className="h-3 w-3" /> Upload Files
                <input type="file" className="hidden" accept=".txt,.json,.md" onChange={handleFileUpload} />
              </label>
            </div>
          </div>

          {showScrapeInput && (
            <div className="mb-3 rounded-xl border border-border bg-secondary/25 p-3">
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_100px_100px]">
                <div className="space-y-1">
                  <label className="block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Start URL</label>
                  <input
                    type="url"
                    value={scrapeUrl}
                    onChange={(e) => setScrapeUrl(e.target.value)}
                    placeholder="https://example.com"
                    className="w-full rounded-md border border-border bg-secondary px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground transition-colors focus:border-primary/50 focus:outline-none"
                  />
                </div>
                <div className="space-y-1">
                  <label className="block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Depth (clicks)</label>
                  <input
                    type="number"
                    min={1}
                    max={3}
                    value={scrapeDepth}
                    onChange={(e) => setScrapeDepth(Math.max(1, Math.min(3, Number(e.target.value) || 1)))}
                    className="w-full rounded-md border border-border bg-secondary px-3 py-2 font-mono text-xs text-foreground"
                  />
                </div>
                <div className="space-y-1">
                  <label className="block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Page limit</label>
                  <input
                    type="number"
                    min={1}
                    max={500}
                    value={scrapeLimit}
                    onChange={(e) => setScrapeLimit(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
                    className="w-full rounded-md border border-border bg-secondary px-3 py-2 font-mono text-xs text-foreground"
                  />
                </div>
              </div>
              <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
                <div className="space-y-1">
                  <label className="block font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Focus keywords</label>
                  <input
                    type="text"
                    value={scrapeFocus}
                    onChange={(e) => setScrapeFocus(e.target.value)}
                    placeholder="hubspot, modules"
                    className="w-full rounded-md border border-border bg-secondary px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground transition-colors focus:border-primary/50 focus:outline-none"
                  />
                </div>
                <label className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={scrapeSitemapOnly}
                    onChange={(e) => setScrapeSitemapOnly(e.target.checked)}
                  />
                  Sitemap only
                </label>
                <button
                  onClick={() => scrapeMutation.mutate(scrapeUrl)}
                  disabled={!scrapeUrl || scrapeMutation.isPending}
                  className="rounded-md bg-primary px-3 py-2 font-mono text-xs text-primary-foreground disabled:opacity-50"
                >
                  {scrapeMutation.isPending ? "Scraping..." : "Scrape"}
                </button>
              </div>
              <p className="mt-2 font-mono text-[10px] text-muted-foreground">
                Priority crawl: breadth-first discovery with T1 pages like about, services, pricing, and case studies ranked first.
              </p>
            </div>
          )}

          {activeScrapeJob ? (
            <div className="mb-3 rounded-xl border border-border bg-background/70 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-wide text-primary">Scrape Progress</p>
                  <p className="mt-1 text-sm text-foreground">
                    {String((scrapeTelemetry.current_url as string) || activeScrapeJob.url || "").slice(0, 160)}
                  </p>
                </div>
                <span className="rounded-full border border-border bg-secondary px-3 py-1 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                  {activeScrapeJob.status}
                </span>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-4">
                <div className="rounded-lg border border-border bg-secondary/35 px-3 py-2">
                  <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Pages found</p>
                  <p className="mt-1 font-mono text-sm text-foreground">{Number(scrapeTelemetry.pages_found || 0)}</p>
                </div>
                <div className="rounded-lg border border-border bg-secondary/35 px-3 py-2">
                  <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Pages scraped</p>
                  <p className="mt-1 font-mono text-sm text-foreground">{activeScrapeJob.pages_scraped}</p>
                </div>
                <div className="rounded-lg border border-border bg-secondary/35 px-3 py-2">
                  <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Depth</p>
                  <p className="mt-1 font-mono text-sm text-foreground">{activeScrapeJob.depth}</p>
                </div>
                <div className="rounded-lg border border-border bg-secondary/35 px-3 py-2">
                  <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Limit</p>
                  <p className="mt-1 font-mono text-sm text-foreground">{activeScrapeJob.limit || scrapeLimit}</p>
                </div>
              </div>
              {activeScrapeJob.summary ? (
                <p className="mt-3 font-mono text-[10px] text-muted-foreground">
                  Scraped {Number(scrapeSummary.total_pages || activeScrapeJob.pages_scraped || 0)}/{Number(activeScrapeJob.limit || scrapeLimit)} pages.
                  {" "}
                  {Number(scrapePriorityCounts.T1 || 0)} T1 pages,
                  {" "}
                  {Number(scrapePriorityCounts.T2 || 0)} T2 pages.
                  {" "}
                  Total words: {Number(scrapeSummary.total_words || 0).toLocaleString()}.
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={knowledgeSearch}
                onChange={(e) => {
                  setKnowledgeSearch(e.target.value);
                  setKnowledgePage(1);
                }}
                placeholder="Search chunks..."
                className="flex-1 rounded-md border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-foreground placeholder:text-muted-foreground transition-colors focus:border-primary/50 focus:outline-none"
              />
              <span className="whitespace-nowrap font-mono text-[10px] text-muted-foreground">
                {knowledgeData?.pagination.total_items ?? 0} chunks
              </span>
            </div>

            <div className="max-h-[480px] space-y-2 overflow-y-auto rounded-xl border border-border bg-secondary/35 p-3">
              {knowledgeLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                </div>
              ) : knowledgeError ? (
                <div className="py-8 text-center">
                  <p className="font-mono text-xs text-destructive">
                    {knowledgeError instanceof Error ? knowledgeError.message : "Could not load knowledge chunks."}
                  </p>
                </div>
              ) : (knowledgeData?.chunks?.length ?? 0) > 0 ? (
                knowledgeData!.chunks.map((chunk) => (
                  <div key={chunk.id} className="rounded-xl border border-border/70 bg-background/70 p-3">
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-[10px] text-primary">{chunk.source || "manual_upload"}</span>
                      <div className="flex items-center gap-2">
                        <span className="whitespace-nowrap font-mono text-[10px] text-muted-foreground">{chunk.word_count} words</span>
                        <button
                          onClick={() => beginChunkEdit(chunk.id, chunk.full_content || chunk.content)}
                          className="rounded-full border border-border bg-secondary/50 px-2 py-1 font-mono text-[10px] text-secondary-foreground transition-colors hover:text-foreground"
                        >
                          <span className="inline-flex items-center gap-1">
                            <Pencil className="h-3 w-3" />
                            Edit
                          </span>
                        </button>
                        <button
                          onClick={() => {
                            if (!window.confirm("Delete this knowledge chunk?")) return;
                            if (selectedChunkId === chunk.id) {
                              setSelectedChunkId(null);
                            }
                            deleteChunkMutation.mutate(chunk.id);
                          }}
                          disabled={deleteChunkMutation.isPending}
                          className="rounded-full border border-destructive/30 bg-destructive/10 px-2 py-1 font-mono text-[10px] text-destructive transition-colors hover:opacity-90 disabled:opacity-50"
                        >
                          <span className="inline-flex items-center gap-1">
                            <Trash2 className="h-3 w-3" />
                            Delete
                          </span>
                        </button>
                        <button
                          onClick={() => {
                            setSelectedChunkId(chunk.id);
                            setSourceView("cleaned");
                          }}
                          className="rounded-full border border-border bg-secondary/50 px-2 py-1 font-mono text-[10px] text-secondary-foreground transition-colors hover:text-foreground"
                        >
                          View Source
                        </button>
                      </div>
                    </div>
                    <div className="mb-2 flex flex-wrap gap-2">
                      {chunk.source_file_path ? (
                        <span className="rounded-full border border-border bg-secondary/50 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                          {chunk.source_file_path}
                        </span>
                      ) : null}
                      {chunk.line_range ? (
                        <span className="rounded-full border border-border bg-secondary/50 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                          lines {chunk.line_range[0]}-{chunk.line_range[1]}
                        </span>
                      ) : null}
                      {chunk.paragraph_range ? (
                        <span className="rounded-full border border-border bg-secondary/50 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                          paras {chunk.paragraph_range[0]}-{chunk.paragraph_range[1]}
                        </span>
                      ) : null}
                    </div>
                    {editingChunkId === chunk.id ? (
                      <div className="space-y-2">
                        <textarea
                          value={editingChunkContent}
                          onChange={(e) => setEditingChunkContent(e.target.value)}
                          rows={7}
                          className="w-full resize-y rounded-lg border border-border bg-card px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground focus:border-primary/50 focus:outline-none"
                        />
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={cancelChunkEdit}
                            className="rounded-lg border border-border bg-secondary px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-secondary-foreground"
                          >
                            Cancel
                          </button>
                          <button
                            onClick={() => updateChunkMutation.mutate({ chunkId: chunk.id, content: editingChunkContent })}
                            disabled={!editingChunkContent.trim() || updateChunkMutation.isPending}
                            className="rounded-lg bg-primary px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-primary-foreground disabled:opacity-50"
                          >
                            {updateChunkMutation.isPending ? "Saving..." : "Save chunk"}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <p className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-foreground">{chunk.content}</p>
                    )}
                  </div>
                ))
              ) : (
                <div className="py-8 text-center font-mono text-xs text-muted-foreground">
                  No knowledge chunks yet. Upload files or scrape a site to populate context.
                </div>
              )}
            </div>

            <div className="flex items-center justify-between">
              <span className="font-mono text-[10px] text-muted-foreground">
                Page {knowledgeData?.pagination.page ?? 1} / {knowledgeData?.pagination.total_pages ?? 1}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setKnowledgePage((p) => Math.max(1, p - 1))}
                  disabled={(knowledgeData?.pagination.page ?? 1) <= 1}
                  className="rounded border border-border bg-secondary px-2 py-1 font-mono text-[10px] text-secondary-foreground disabled:opacity-50"
                >
                  Prev
                </button>
                <button
                  onClick={() => setKnowledgePage((p) => p + 1)}
                  disabled={(knowledgeData?.pagination.page ?? 1) >= (knowledgeData?.pagination.total_pages ?? 1)}
                  className="rounded border border-border bg-secondary px-2 py-1 font-mono text-[10px] text-secondary-foreground disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          </div>
        </motion.div>
      )}

      {selectedChunkId ? (
        <div className="fixed inset-0 z-50 flex items-stretch justify-end bg-ink/35 backdrop-blur-sm">
          <div className="h-full w-full max-w-3xl overflow-hidden border-l border-border bg-background shadow-2xl">
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Trace Viewer</p>
                <p className="text-sm font-semibold text-foreground">{selectedChunkSource?.source_file_path || selectedChunkId}</p>
              </div>
              <button
                onClick={() => setSelectedChunkId(null)}
                className="rounded-full border border-border bg-secondary/50 p-2 text-muted-foreground transition-colors hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex items-center gap-2 border-b border-border px-5 py-3">
              <button
                onClick={() => setSourceView("cleaned")}
                className={`rounded-full border px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide ${
                  sourceView === "cleaned" ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-secondary text-muted-foreground"
                }`}
              >
                Cleaned for LLM
              </button>
              <button
                onClick={() => setSourceView("raw")}
                className={`rounded-full border px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide ${
                  sourceView === "raw" ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-secondary text-muted-foreground"
                }`}
              >
                Raw Local Source
              </button>
              {selectedChunkSource?.metadata?.line_range ? (
                <span className="rounded-full border border-border bg-secondary/50 px-3 py-1.5 font-mono text-[10px] text-muted-foreground">
                  lines {(selectedChunkSource.metadata.line_range as number[])[0]}-{(selectedChunkSource.metadata.line_range as number[])[1]}
                </span>
              ) : null}
            </div>
            <div className="grid h-[calc(100%-122px)] gap-0 lg:grid-cols-[280px_minmax(0,1fr)]">
              <div className="border-b border-border bg-secondary/25 p-4 lg:border-b-0 lg:border-r">
                <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Source Metadata</p>
                <div className="mt-3 space-y-2">
                  <div>
                    <p className="font-mono text-[10px] text-muted-foreground">URL</p>
                    <p className="break-all text-xs text-foreground">{String(selectedChunkSource?.metadata?.url || "-")}</p>
                  </div>
                  <div>
                    <p className="font-mono text-[10px] text-muted-foreground">Title</p>
                    <p className="text-xs text-foreground">{String(selectedChunkSource?.metadata?.title || "-")}</p>
                  </div>
                  <div>
                    <p className="font-mono text-[10px] text-muted-foreground">File</p>
                    <p className="break-all font-mono text-[11px] text-foreground">{selectedChunkSource?.source_file_path || "-"}</p>
                  </div>
                </div>
              </div>
              <div className="overflow-y-auto p-4">
                {selectedChunkSourceLoading ? (
                  <div className="flex items-center justify-center py-10">
                    <Loader2 className="h-5 w-5 animate-spin text-primary" />
                  </div>
                ) : selectedChunkSourceError ? (
                  <div className="flex items-center justify-center py-10">
                    <p className="font-mono text-xs text-destructive">
                      {selectedChunkSourceError instanceof Error ? selectedChunkSourceError.message : "Could not load chunk source."}
                    </p>
                  </div>
                ) : (
                  <pre className="whitespace-pre-wrap break-words rounded-xl border border-border bg-secondary/25 p-4 font-mono text-[11px] leading-relaxed text-foreground">
                    {sourceView === "cleaned"
                      ? selectedChunkSource?.cleaned_text || "No cleaned chunk available."
                      : selectedChunkSource?.raw_text || selectedChunkSource?.raw_html || "No local source file available."}
                  </pre>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {activeTab === "preview" && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
          <div className="mb-4 flex items-center justify-between rounded-[1rem] border border-border bg-card p-4 surface-shadow">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-wide text-primary">Test Chat</p>
              <p className="mt-1 text-sm text-muted-foreground">Run a live preview, then jump back to the buyer overview without losing your place.</p>
            </div>
            <button
              onClick={() => setActiveTab("overview")}
              className="flex items-center gap-2 rounded-xl border border-border bg-secondary px-3 py-2 text-sm text-secondary-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </button>
          </div>
          <EmbedChatPreview
            botName={editBot.name || "HubSpot Specialist"}
            botId={editBot.id}
            accentColor={editBot.accent_color}
            mode="admin"
            welcomeMessage={editBot.welcome_message}
            initialQuickChips={editBot.quick_chip_settings.initial_chips}
          />
        </motion.div>
      )}

      {activeTab === "technical" && (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
          <div className="space-y-5">
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="rounded-[1rem] border border-border bg-card p-4 surface-shadow">
              <label className="mb-2 block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">System Prompt</label>
              <textarea
                value={editBot.system_prompt}
                onChange={(e) => setEditBot({ ...editBot, system_prompt: e.target.value })}
                rows={6}
                className="w-full resize-none rounded-md border border-border bg-secondary p-3 font-mono text-xs text-foreground transition-colors focus:border-primary/50 focus:outline-none"
              />
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="rounded-[1rem] border border-border bg-card p-4 surface-shadow">
              <label className="mb-3 block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Configuration</label>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="mb-1 block font-mono text-[10px] text-muted-foreground">Temperature</label>
                  <div className="flex items-center gap-2">
                    <Thermometer className="h-3.5 w-3.5 text-warning" />
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={editBot.temperature}
                      onChange={(e) => setEditBot({ ...editBot, temperature: parseFloat(e.target.value) })}
                      className="flex-1 accent-primary"
                    />
                    <span className="w-8 font-mono text-xs text-foreground">{editBot.temperature}</span>
                  </div>
                </div>

                <div>
                  <label className="mb-1 block font-mono text-[10px] text-muted-foreground">Memory</label>
                  <button
                    onClick={() => setEditBot({ ...editBot, memory_enabled: !editBot.memory_enabled })}
                    className={`flex items-center gap-2 rounded-md border px-3 py-1.5 font-mono text-xs transition-all ${
                      editBot.memory_enabled
                        ? "border-accent/30 bg-accent/10 text-accent"
                        : "border-border bg-secondary text-muted-foreground"
                    }`}
                  >
                    <Brain className="h-3.5 w-3.5" />
                    {editBot.memory_enabled ? "Enabled" : "Disabled"}
                  </button>
                </div>

                <div>
                  <label className="mb-1 block font-mono text-[10px] text-muted-foreground">Status</label>
                  <button
                    onClick={() =>
                      setEditBot({
                        ...editBot,
                        status: editBot.status === "active" ? "inactive" : "active",
                      })
                    }
                    className={`flex items-center gap-2 rounded-md border px-3 py-1.5 font-mono text-xs transition-all ${
                      editBot.status === "active"
                        ? "border-success/30 bg-success/10 text-success"
                        : editBot.status === "expired"
                          ? "border-destructive/30 bg-destructive/10 text-destructive"
                          : "border-border bg-secondary text-muted-foreground"
                    }`}
                  >
                    {editBot.status === "active" ? <ToggleRight className="h-3.5 w-3.5" /> : <ToggleLeft className="h-3.5 w-3.5" />}
                    {editBot.status === "active" ? "Active" : editBot.status === "expired" ? "Expired" : "Inactive"}
                  </button>
                </div>

                <div>
                  <label className="mb-1 block font-mono text-[10px] text-muted-foreground">Message Limit</label>
                  <input
                    type="number"
                    value={editBot.message_limit ?? ""}
                    onChange={(e) => setEditBot({ ...editBot, message_limit: e.target.value ? parseInt(e.target.value) : null })}
                    placeholder="Unlimited"
                    className="w-full rounded-md border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-foreground placeholder:text-muted-foreground transition-colors focus:border-primary/50 focus:outline-none"
                  />
                </div>

              </div>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
              <BotToolingPanel bot={editBot} toolCatalog={toolCatalog} onChange={setEditBot} />
            </motion.div>
          </div>

          <div className="space-y-5">
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="space-y-3 rounded-[1rem] border border-border bg-card p-4 surface-shadow">
              <label className="block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Endpoints</label>
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-mono text-[10px] text-muted-foreground">API Endpoint</span>
                  <button onClick={() => copyToClipboard(apiEndpoint, "API endpoint")} className="text-muted-foreground hover:text-foreground">
                    <Copy className="h-3 w-3" />
                  </button>
                </div>
                <code className="block break-all rounded border border-border bg-secondary px-2 py-1.5 font-mono text-[10px] text-primary">{apiEndpoint}</code>
              </div>
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-mono text-[10px] text-muted-foreground">Public Tunnel</span>
                  <button
                    onClick={() => cloudflareEndpoint && copyToClipboard(cloudflareEndpoint, "Public tunnel endpoint")}
                    disabled={!cloudflareEndpoint}
                    className="text-muted-foreground hover:text-foreground disabled:opacity-40"
                  >
                    <Copy className="h-3 w-3" />
                  </button>
                </div>
                <code className="block break-all rounded border border-border bg-secondary px-2 py-1.5 font-mono text-[10px] text-primary">
                  {cloudflareEndpoint || "Public tunnel not ready yet. It starts automatically with server startup."}
                </code>
                {!cloudflareStable && cloudflareEndpoint && (
                  <p className="mt-1 font-mono text-[10px] text-warning">
                    Non-stable tunnel detected: URL may change on restart. Configure a fixed subdomain or domain in .env.
                  </p>
                )}
                {cloudflareEndpoint && tunnelTokenRequired && (
                  <p className="mt-1 font-mono text-[10px] text-accent">Token has been appended automatically for current HubSpot/module compatibility.</p>
                )}
              </div>
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-mono text-[10px] text-muted-foreground">HubSpot Module Snippet</span>
                  <button onClick={() => copyToClipboard(hubspotModuleSnippet, "HubSpot module snippet")} className="text-muted-foreground hover:text-foreground">
                    <Copy className="h-3 w-3" />
                  </button>
                </div>
                <pre className="block overflow-x-auto whitespace-pre-wrap rounded border border-border bg-secondary px-2 py-1.5 font-mono text-[10px] text-primary">{hubspotModuleSnippet}</pre>
              </div>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="space-y-3 rounded-[1rem] border border-border bg-card p-4 surface-shadow">
              <label className="block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Model Files Folder</label>
              <code className="block break-all rounded border border-border bg-secondary px-2 py-1.5 font-mono text-[10px] text-primary">
                {modelFolder?.path || "Loading folder path..."}
              </code>
              <button
                onClick={() => openFolderMutation.mutate()}
                disabled={openFolderMutation.isPending}
                className="flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-secondary-foreground hover:text-foreground disabled:opacity-50"
              >
                {openFolderMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <FolderOpen className="h-3 w-3" />}
                Open Folder
              </button>
            </motion.div>
          </div>
        </div>
      )}

      <div className="sticky bottom-3 z-20 mt-6">
        <div className="flex items-center justify-between gap-3 rounded-[1rem] border border-border bg-card/95 px-4 py-3 shadow-lg backdrop-blur">
          <div className="hidden sm:block">
            <p className="font-mono text-[10px] uppercase tracking-wide text-primary">Quick Actions</p>
            <p className="text-xs text-muted-foreground">Save changes, test the chat, or copy the publish snippet without leaving this screen.</p>
          </div>
          <div className="flex flex-1 items-center justify-end gap-2">
            <button
              onClick={() => setActiveTab("preview")}
              className="flex items-center gap-1.5 rounded-xl border border-border bg-secondary px-3 py-2 text-sm text-secondary-foreground transition-colors hover:text-foreground"
            >
              <MessageSquare className="h-4 w-4" />
              Test Chat
            </button>
            <button
              onClick={() => copyToClipboard(hubspotModuleSnippet, "HubSpot module snippet")}
              className="flex items-center gap-1.5 rounded-xl border border-border bg-secondary px-3 py-2 text-sm text-secondary-foreground transition-colors hover:text-foreground"
            >
              <Rocket className="h-4 w-4" />
              Publish
            </button>
            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending}
              className="flex items-center gap-1.5 rounded-xl bg-primary px-3 py-2 text-sm text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
