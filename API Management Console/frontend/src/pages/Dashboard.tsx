import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { SidebarNav } from "@/components/SidebarNav";
import { BotListView } from "@/components/BotListView";
import { BotDetailView } from "@/components/BotDetailView";
import { AnalyticsView } from "@/components/AnalyticsView";
import { CreateBotView } from "@/components/CreateBotView";
import { SettingsView } from "@/components/SettingsView";
import { APIKeysView } from "@/components/APIKeysView";
import { fetchBots, cloneBot, toggleBot, getSystemStatus } from "@/lib/api";
import { Bot } from "@/types/bot";
import { toast } from "sonner";
import { Activity, Lock, ShieldCheck, Sparkles, Globe, Timer } from "lucide-react";

function formatUptime(totalSeconds?: number) {
  const seconds = Math.max(0, totalSeconds || 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

function formatCheckTime(iso?: string | null) {
  if (!iso) return "Pending";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "Pending";
  return parsed.toLocaleString();
}

export function Dashboard() {
  const [view, setView] = useState("bots");
  const [selectedBot, setSelectedBot] = useState<Bot | null>(null);
  const location = useLocation();
  const queryClient = useQueryClient();
  const [forcePasswordSetup, setForcePasswordSetup] = useState(
    sessionStorage.getItem("force_password_setup") === "1"
  );

  const { data: botsData, isLoading } = useQuery({
    queryKey: ["bots"],
    queryFn: fetchBots,
  });

  const { data: systemStatus } = useQuery({
    queryKey: ["system-status"],
    queryFn: getSystemStatus,
    refetchInterval: 30000,
  });

  const bots = botsData?.bots || [];

  useEffect(() => {
    const state = location.state as { initialView?: string; forcePasswordSetup?: boolean } | null;
    if (state?.forcePasswordSetup) {
      sessionStorage.setItem("force_password_setup", "1");
      setForcePasswordSetup(true);
      setView("settings");
    } else if (state?.initialView) {
      setView(state.initialView);
    }
  }, [location.state]);

  useEffect(() => {
    if (systemStatus?.requires_password_change) {
      sessionStorage.setItem("force_password_setup", "1");
      setForcePasswordSetup(true);
      setView("settings");
      return;
    }
    if (forcePasswordSetup && systemStatus && !systemStatus.requires_password_change) {
      sessionStorage.removeItem("force_password_setup");
      setForcePasswordSetup(false);
    }
  }, [systemStatus, forcePasswordSetup]);

  const cloneMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name?: string }) => cloneBot(id, name),
    onSuccess: (newBot) => {
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      toast.success(`Cloned to "${newBot.name}"`);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Clone failed");
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (id: string) => toggleBot(id),
    onSuccess: (updatedBot) => {
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      toast.info(`${updatedBot.name} ${updatedBot.status === "active" ? "enabled" : "disabled"}`);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Toggle failed");
    },
  });

  const handleSelectBot = (bot: Bot) => {
    setSelectedBot(bot);
    setView("detail");
  };

  const handleClone = (bot: Bot) => {
    cloneMutation.mutate({ id: bot.id, name: `${bot.name} (Clone)` });
  };

  const handleToggle = (bot: Bot) => {
    toggleMutation.mutate(bot.id);
  };

  const handleBotSaved = (bot: Bot) => {
    queryClient.invalidateQueries({ queryKey: ["bots"] });
    setSelectedBot(bot);
  };

  const trustItems = useMemo(() => ([
    {
      label: "Encrypted",
      value: systemStatus?.encryption_enabled ? "Enabled" : "Review",
      ok: !!systemStatus?.encryption_enabled,
      icon: ShieldCheck,
    },
    {
      label: "Knowledge Indexed",
      value: systemStatus?.faiss === "Loaded" ? "Indexed" : "Pending",
      ok: systemStatus?.faiss === "Loaded",
      icon: Sparkles,
    },
    {
      label: "HubSpot Ready",
      value: systemStatus?.api_gateway_running ? "Ready" : "Configuring",
      ok: !!systemStatus?.api_gateway_running,
      icon: Lock,
    },
    {
      label: "Hot",
      value: systemStatus?.local_llm === "Running" ? "Model hot" : "Warming",
      ok: systemStatus?.local_llm === "Running",
      icon: Activity,
    },
    {
      label: "Tunnel Stable",
      value: systemStatus?.public_tunnel_ready ? (systemStatus?.tunnel_stable ? "Stable" : "Live") : "Local only",
      ok: !!systemStatus?.public_tunnel_ready,
      icon: Globe,
    },
    {
      label: "Uptime",
      value: formatUptime(systemStatus?.uptime_seconds),
      ok: true,
      icon: Timer,
    },
  ]), [systemStatus]);

  const handleViewChange = (nextView: string) => {
    if (forcePasswordSetup && nextView !== "settings") {
      toast.error("Update the admin password before using the rest of the portal.");
      setView("settings");
      setSelectedBot(null);
      return;
    }
    setView(nextView);
    setSelectedBot(null);
  };

  return (
    <div className="min-h-screen bg-background">
      <SidebarNav
        activeView={view === "detail" ? "bots" : view}
        onViewChange={handleViewChange}
      />

      <main className="p-6 pl-16 lg:p-8 lg:pl-[19rem]">
        <div className="mb-6 rounded-[1.25rem] border border-border bg-card/90 p-4 surface-shadow">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Arctic Studio Assurance</p>
              <h2 className="mt-1 text-lg font-semibold text-foreground">HubSpot AI Operations</h2>
              <p className="text-xs text-muted-foreground">
                {`${systemStatus?.current_deployment_url || "http://localhost:8000/admin"} · Last system check ${formatCheckTime(systemStatus?.last_system_check_at)}`}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-6">
              {trustItems.map((item) => (
                <div key={item.label} className="rounded-2xl border border-border bg-secondary/35 px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <item.icon className={`h-3.5 w-3.5 ${item.ok ? "text-primary" : "text-warning"}`} />
                    <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">{item.label}</span>
                  </div>
                  <p className={`mt-1 text-xs font-medium ${item.ok ? "text-foreground" : "text-warning"}`}>{item.value}</p>
                </div>
              ))}
            </div>
          </div>
          {forcePasswordSetup && (
            <div className="mt-3 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
              First-run security action required. Update the default admin password before continuing.
            </div>
          )}
        </div>

        {view === "bots" && (
          <BotListView
            bots={bots}
            isLoading={isLoading}
            onSelectBot={handleSelectBot}
            onCloneBot={handleClone}
            onToggleBot={handleToggle}
            systemStatus={systemStatus}
          />
        )}

        {view === "detail" && selectedBot && (
          <BotDetailView
            bot={selectedBot}
            onBack={() => setView("bots")}
            onSave={handleBotSaved}
            onClone={handleClone}
            shellStatus={systemStatus}
          />
        )}

        {view === "analytics" && <AnalyticsView />}
        {view === "create" && <CreateBotView onCreated={() => setView("bots")} />}
        {view === "api-keys" && <APIKeysView />}
        {view === "settings" && <SettingsView forcePasswordSetup={forcePasswordSetup} />}
      </main>
    </div>
  );
}
