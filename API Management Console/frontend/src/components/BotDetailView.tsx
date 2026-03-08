import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Bot } from "@/types/bot";
import {
  updateBot,
  uploadDocument,
  rebuildEmbeddings,
  scrapeWebsite,
  fetchKnowledgeChunks,
  getSystemStatus,
  expireBot,
  recoverBot,
  getBotModelFilesFolder,
  openBotModelFilesFolder,
} from "@/lib/api";
import { toast } from "sonner";
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
} from "lucide-react";

interface BotDetailViewProps {
  bot: Bot;
  onBack: () => void;
  onSave: (bot: Bot) => void;
  onClone: (bot: Bot) => void;
}

export function BotDetailView({ bot, onBack, onSave, onClone }: BotDetailViewProps) {
  const [editBot, setEditBot] = useState<Bot>({ ...bot });
  const [scrapeUrl, setScrapeUrl] = useState("");
  const [showScrapeInput, setShowScrapeInput] = useState(false);
  const [knowledgeSearch, setKnowledgeSearch] = useState("");
  const [knowledgePage, setKnowledgePage] = useState(1);
  const queryClient = useQueryClient();

  useEffect(() => {
    setEditBot({ ...bot });
  }, [bot]);

  const { data: systemStatus } = useQuery({
    queryKey: ["system-status"],
    queryFn: getSystemStatus,
    refetchInterval: 30000,
  });

  const { data: knowledgeData, isLoading: knowledgeLoading } = useQuery({
    queryKey: ["knowledge-chunks", bot.id, knowledgePage, knowledgeSearch],
    queryFn: () => fetchKnowledgeChunks(bot.id, knowledgePage, 8, knowledgeSearch),
    enabled: !!bot.id,
  });

  const { data: modelFolder } = useQuery({
    queryKey: ["bot-model-folder", bot.id],
    queryFn: () => getBotModelFilesFolder(bot.id),
    enabled: !!bot.id,
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
    onSuccess: () => {
      toast.success("Opened model files folder");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Could not open folder");
    },
  });

  const rebuildMutation = useMutation({
    mutationFn: () => rebuildEmbeddings(bot.id),
    onSuccess: () => {
      toast.success("Embeddings rebuilt");
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunks", bot.id] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Rebuild failed");
    },
  });

  const scrapeMutation = useMutation({
    mutationFn: (url: string) => scrapeWebsite(bot.id, url, 2),
    onSuccess: (data) => {
      toast.success(`Scraper job started (ID: ${data.id})`);
      setShowScrapeInput(false);
      setScrapeUrl("");
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-chunks", bot.id] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Scrape failed");
    },
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

  const embedSnippet = `<iframe src="${window.location.origin}/embed/${bot.id}" width="100%" height="600" frameborder="0"></iframe>`;
  const apiEndpoint = `${window.location.origin}/api/bot/${bot.id}/chat`;
  const publicBaseUrl = systemStatus?.public_base_url?.replace(/\/$/, "");
  const cloudflareStable = systemStatus?.cloudflare_stable ?? false;
  const cloudflareEndpoint = publicBaseUrl ? `${publicBaseUrl}/api/bot/${bot.id}/chat` : null;

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text).then(() => {
      toast.success(`${label} copied to clipboard`);
    });
  };

  return (
    <div>
      <button onClick={onBack} className="flex items-center gap-2 text-muted-foreground hover:text-foreground font-mono text-xs mb-4 transition-colors">
        <ArrowLeft className="w-3.5 h-3.5" /> Back to Fleet
      </button>

      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="font-mono text-xl font-bold text-foreground">{editBot.name}</h1>
          <p className="font-mono text-xs text-muted-foreground mt-1">{bot.id} · {bot.provider_type}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => onClone(bot)}
            className="flex items-center gap-1.5 font-mono text-xs px-3 py-1.5 rounded-md border border-border bg-secondary text-secondary-foreground hover:text-foreground transition-colors"
          >
            <Copy className="w-3 h-3" /> Clone
          </button>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-1.5 font-mono text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {saveMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            Save
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-5">
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="bg-card border border-border rounded-lg p-4">
            <label className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider mb-2 block">System Prompt</label>
            <textarea
              value={editBot.system_prompt}
              onChange={(e) => setEditBot({ ...editBot, system_prompt: e.target.value })}
              rows={5}
              className="w-full bg-secondary border border-border rounded-md p-3 font-mono text-xs text-foreground resize-none focus:outline-none focus:border-primary/50 transition-colors"
            />
          </motion.div>

          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="bg-card border border-border rounded-lg p-4">
            <label className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider mb-3 block">Configuration</label>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="font-mono text-[10px] text-muted-foreground mb-1 block">Temperature</label>
                <div className="flex items-center gap-2">
                  <Thermometer className="w-3.5 h-3.5 text-warning" />
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.1"
                    value={editBot.temperature}
                    onChange={(e) => setEditBot({ ...editBot, temperature: parseFloat(e.target.value) })}
                    className="flex-1 accent-primary"
                  />
                  <span className="font-mono text-xs text-foreground w-8">{editBot.temperature}</span>
                </div>
              </div>

              <div>
                <label className="font-mono text-[10px] text-muted-foreground mb-1 block">Memory</label>
                <button
                  onClick={() => setEditBot({ ...editBot, memory_enabled: !editBot.memory_enabled })}
                  className={`flex items-center gap-2 font-mono text-xs px-3 py-1.5 rounded-md border transition-all ${
                    editBot.memory_enabled
                      ? "bg-accent/10 text-accent border-accent/30"
                      : "bg-secondary text-muted-foreground border-border"
                  }`}
                >
                  <Brain className="w-3.5 h-3.5" />
                  {editBot.memory_enabled ? "Enabled" : "Disabled"}
                </button>
              </div>

              <div>
                <label className="font-mono text-[10px] text-muted-foreground mb-1 block">Status</label>
                <button
                  onClick={() =>
                    setEditBot({
                      ...editBot,
                      status: editBot.status === "active" ? "inactive" : "active",
                    })
                  }
                  className={`flex items-center gap-2 font-mono text-xs px-3 py-1.5 rounded-md border transition-all ${
                    editBot.status === "active"
                      ? "bg-success/10 text-success border-success/30"
                      : editBot.status === "expired"
                        ? "bg-destructive/10 text-destructive border-destructive/30"
                        : "bg-secondary text-muted-foreground border-border"
                  }`}
                >
                  {editBot.status === "active" ? <ToggleRight className="w-3.5 h-3.5" /> : <ToggleLeft className="w-3.5 h-3.5" />}
                  {editBot.status === "active" ? "Active" : editBot.status === "expired" ? "Expired" : "Inactive"}
                </button>
              </div>

              <div>
                <label className="font-mono text-[10px] text-muted-foreground mb-1 block">Message Limit</label>
                <input
                  type="number"
                  value={editBot.message_limit ?? ""}
                  onChange={(e) => setEditBot({ ...editBot, message_limit: e.target.value ? parseInt(e.target.value) : null })}
                  placeholder="Unlimited"
                  className="w-full bg-secondary border border-border rounded-md px-3 py-1.5 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors"
                />
              </div>
            </div>

            <div className="flex items-center gap-2 mt-4 pt-4 border-t border-border">
              <button
                onClick={() => expireMutation.mutate()}
                disabled={expireMutation.isPending || editBot.status === "expired"}
                className="flex items-center gap-1.5 font-mono text-[10px] px-2 py-1 rounded border border-destructive/40 bg-destructive/10 text-destructive disabled:opacity-50"
              >
                {expireMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Archive className="w-3 h-3" />}
                Expire Bot
              </button>
              <button
                onClick={() => recoverMutation.mutate()}
                disabled={recoverMutation.isPending || editBot.status === "active"}
                className="flex items-center gap-1.5 font-mono text-[10px] px-2 py-1 rounded border border-success/30 bg-success/10 text-success disabled:opacity-50"
              >
                {recoverMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
                Recover To Active
              </button>
            </div>
          </motion.div>

          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="bg-card border border-border rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <label className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">Knowledge Base</label>
              <div className="flex gap-2">
                <button
                  onClick={() => setShowScrapeInput(!showScrapeInput)}
                  className="flex items-center gap-1.5 font-mono text-[10px] px-2 py-1 rounded border border-border bg-secondary text-secondary-foreground hover:text-foreground transition-colors"
                >
                  <Globe className="w-3 h-3" /> Scrape Website
                </button>
                <label className="flex items-center gap-1.5 font-mono text-[10px] px-2 py-1 rounded border border-border bg-secondary text-secondary-foreground hover:text-foreground transition-colors cursor-pointer">
                  <Upload className="w-3 h-3" /> Upload Files
                  <input type="file" className="hidden" accept=".txt,.json,.md" onChange={handleFileUpload} />
                </label>
                <button
                  onClick={() => rebuildMutation.mutate()}
                  disabled={rebuildMutation.isPending}
                  className="flex items-center gap-1.5 font-mono text-[10px] px-2 py-1 rounded border border-primary/30 bg-primary/10 text-primary transition-colors disabled:opacity-50"
                >
                  {rebuildMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                  Rebuild Index
                </button>
              </div>
            </div>

            {showScrapeInput && (
              <div className="flex gap-2 mb-3">
                <input
                  type="url"
                  value={scrapeUrl}
                  onChange={(e) => setScrapeUrl(e.target.value)}
                  placeholder="https://example.com"
                  className="flex-1 bg-secondary border border-border rounded-md px-3 py-1.5 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors"
                />
                <button
                  onClick={() => scrapeMutation.mutate(scrapeUrl)}
                  disabled={!scrapeUrl || scrapeMutation.isPending}
                  className="font-mono text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground disabled:opacity-50"
                >
                  {scrapeMutation.isPending ? "Scraping..." : "Scrape"}
                </button>
              </div>
            )}

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
                  className="flex-1 bg-secondary border border-border rounded-md px-3 py-1.5 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors"
                />
                <span className="font-mono text-[10px] text-muted-foreground whitespace-nowrap">
                  {knowledgeData?.pagination.total_items ?? 0} chunks
                </span>
              </div>

              <div className="bg-secondary/40 border border-border rounded-md p-2 max-h-72 overflow-y-auto space-y-2">
                {knowledgeLoading ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-4 h-4 text-primary animate-spin" />
                  </div>
                ) : (knowledgeData?.chunks?.length ?? 0) > 0 ? (
                  knowledgeData!.chunks.map((chunk) => (
                    <div key={chunk.id} className="border border-border/70 rounded-md p-2 bg-background/70">
                      <div className="flex items-center justify-between mb-1 gap-2">
                        <span className="font-mono text-[10px] text-primary truncate">{chunk.source || "manual_upload"}</span>
                        <span className="font-mono text-[10px] text-muted-foreground whitespace-nowrap">{chunk.word_count} words</span>
                      </div>
                      <p className="font-mono text-[11px] text-foreground leading-relaxed whitespace-pre-wrap">{chunk.content}</p>
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
                    className="font-mono text-[10px] px-2 py-1 rounded border border-border bg-secondary text-secondary-foreground disabled:opacity-50"
                  >
                    Prev
                  </button>
                  <button
                    onClick={() => setKnowledgePage((p) => p + 1)}
                    disabled={(knowledgeData?.pagination.page ?? 1) >= (knowledgeData?.pagination.total_pages ?? 1)}
                    className="font-mono text-[10px] px-2 py-1 rounded border border-border bg-secondary text-secondary-foreground disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            </div>
          </motion.div>
        </div>

        <div className="space-y-5">
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="bg-card border border-border rounded-lg p-4 space-y-3">
            <label className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider block">Statistics</label>
            {[
              { icon: MessageSquare, label: "Messages", value: (bot.messages_used || 0).toLocaleString(), color: "text-primary" },
              { icon: Zap, label: "Tokens", value: `${((bot.token_usage || 0) / 1000).toFixed(1)}k`, color: "text-warning" },
              { icon: Clock, label: "Avg Latency", value: `${(bot.avg_latency || 0).toFixed(1)}s`, color: "text-accent" },
              { icon: Calendar, label: "Created", value: new Date(bot.created_at).toLocaleDateString(), color: "text-muted-foreground" },
            ].map((stat) => (
              <div key={stat.label} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <stat.icon className={`w-3.5 h-3.5 ${stat.color}`} />
                  <span className="font-mono text-xs text-muted-foreground">{stat.label}</span>
                </div>
                <span className="font-mono text-xs text-foreground">{stat.value}</span>
              </div>
            ))}
          </motion.div>

          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="bg-card border border-border rounded-lg p-4 space-y-3">
            <label className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider block">Endpoints</label>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-[10px] text-muted-foreground">API</span>
                <button onClick={() => copyToClipboard(apiEndpoint, "API endpoint")} className="text-muted-foreground hover:text-foreground transition-colors">
                  <Copy className="w-3 h-3" />
                </button>
              </div>
              <code className="block bg-secondary border border-border rounded px-2 py-1.5 font-mono text-[10px] text-primary break-all">{apiEndpoint}</code>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-[10px] text-muted-foreground">Public Tunnel (HubSpot)</span>
                <button
                  onClick={() => cloudflareEndpoint && copyToClipboard(cloudflareEndpoint, "Public tunnel endpoint")}
                  disabled={!cloudflareEndpoint}
                  className="text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40"
                >
                  <Copy className="w-3 h-3" />
                </button>
              </div>
              <code className="block bg-secondary border border-border rounded px-2 py-1.5 font-mono text-[10px] text-primary break-all">
                {cloudflareEndpoint || "Cloudflare tunnel not ready yet. It starts automatically with server startup."}
              </code>
              {!cloudflareStable && cloudflareEndpoint && (
                <p className="font-mono text-[10px] text-warning mt-1">
                  Non-stable tunnel detected: URL may change on restart. Configure fixed subdomain/domain in .env for stable HubSpot endpoint.
                </p>
              )}
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-[10px] text-muted-foreground">Embed</span>
                <button onClick={() => copyToClipboard(embedSnippet, "Embed snippet")} className="text-muted-foreground hover:text-foreground transition-colors">
                  <Copy className="w-3 h-3" />
                </button>
              </div>
              <code className="block bg-secondary border border-border rounded px-2 py-1.5 font-mono text-[10px] text-primary break-all">{embedSnippet}</code>
            </div>
          </motion.div>

          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="bg-card border border-border rounded-lg p-4 space-y-3">
            <label className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider block">Model Files Folder</label>
            <code className="block bg-secondary border border-border rounded px-2 py-1.5 font-mono text-[10px] text-primary break-all">
              {modelFolder?.path || "Loading folder path..."}
            </code>
            <button
              onClick={() => openFolderMutation.mutate()}
              disabled={openFolderMutation.isPending}
              className="flex items-center gap-1.5 font-mono text-xs px-3 py-1.5 rounded-md border border-border bg-secondary text-secondary-foreground hover:text-foreground disabled:opacity-50"
            >
              {openFolderMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <FolderOpen className="w-3 h-3" />}
              Open Folder
            </button>
          </motion.div>
        </div>
      </div>
    </div>
  );
}
