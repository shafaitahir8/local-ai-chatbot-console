import { useState } from "react";
import { Bot } from "@/types/bot";
import { BotCard } from "@/components/BotCard";
import { Search, Loader2, ShieldCheck, Sparkles, Activity, Globe } from "lucide-react";

interface BotListViewProps {
  bots: Bot[];
  isLoading: boolean;
  onSelectBot: (bot: Bot) => void;
  onCloneBot: (bot: Bot) => void;
  onToggleBot: (bot: Bot) => void;
  systemStatus?: {
    encryption_enabled?: boolean;
    faiss?: string;
    local_llm?: string;
    public_tunnel_ready?: boolean;
    current_deployment_url?: string | null;
    last_system_check_at?: string | null;
  };
}

export function BotListView({
  bots,
  isLoading,
  onSelectBot,
  onCloneBot,
  onToggleBot,
  systemStatus,
}: BotListViewProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<string>("all");

  const filtered = bots.filter((b) => {
    const matchesSearch = b.name.toLowerCase().includes(search.toLowerCase()) || b.id.includes(search);
    const matchesFilter = filter === "all" || b.status === filter;
    return matchesSearch && matchesFilter;
  });

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6 rounded-[1.25rem] border border-border bg-card px-5 py-5 surface-shadow">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-primary">Premium HubSpot Specialist</p>
            <h1 className="mt-2 text-2xl font-semibold text-foreground">HubSpot AI Operations</h1>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Private AI for CMS modules, HubL, themes, integrations, and RevOps workflows. Built to feel safe, stable,
              and enterprise-ready in front of U.S. B2B clients.
            </p>
            <p className="mt-3 font-mono text-[11px] text-muted-foreground">
              {`${bots.length} specialists registered · ${bots.filter((b) => b.status === "active").length} active · Deployment ${
                systemStatus?.current_deployment_url || "http://localhost:8000/admin"
              }`}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 lg:w-[520px] lg:grid-cols-3">
            {[
              {
                label: "Secure",
                value: systemStatus?.encryption_enabled ? "Protected" : "Review setup",
                ok: !!systemStatus?.encryption_enabled,
                icon: ShieldCheck,
              },
              {
                label: "Index Fresh",
                value: systemStatus?.faiss === "Loaded" ? "Ready" : "Pending",
                ok: systemStatus?.faiss === "Loaded",
                icon: Sparkles,
              },
              {
                label: "Model Hot",
                value: systemStatus?.local_llm === "Running" ? "Ready" : "Warming",
                ok: systemStatus?.local_llm === "Running",
                icon: Activity,
              },
              {
                label: "Encrypted Keys",
                value: systemStatus?.encryption_enabled ? "Enabled" : "Pending",
                ok: !!systemStatus?.encryption_enabled,
                icon: ShieldCheck,
              },
              {
                label: "Last Sync",
                value: systemStatus?.last_system_check_at
                  ? new Date(systemStatus.last_system_check_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                  : "Pending",
                ok: !!systemStatus?.last_system_check_at,
                icon: Sparkles,
              },
              {
                label: "Public Endpoint Stable",
                value: systemStatus?.public_tunnel_ready ? "Available" : "Local only",
                ok: !!systemStatus?.public_tunnel_ready,
                icon: Globe,
              },
            ].map((item) => (
              <div key={item.label} className="rounded-2xl border border-border bg-secondary/35 px-3 py-3">
                <div className="flex items-center gap-2">
                  <item.icon className={`h-3.5 w-3.5 ${item.ok ? "text-primary" : "text-warning"}`} />
                  <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">{item.label}</span>
                </div>
                <p className={`mt-1 text-xs font-medium ${item.ok ? "text-foreground" : "text-warning"}`}>{item.value}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search specialists..."
            className="w-full rounded-xl border border-border bg-background py-2.5 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground transition-all focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {["all", "active", "inactive", "expired"].map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-full border px-3 py-1.5 font-mono text-[10px] transition-all ${
                filter === f
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "border-border bg-secondary text-muted-foreground hover:text-foreground"
              }`}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {filtered.map((bot, i) => (
          <BotCard
            key={bot.id}
            bot={bot}
            index={i}
            onSelect={onSelectBot}
            onClone={onCloneBot}
            onToggle={onToggleBot}
          />
        ))}
      </div>

      {filtered.length === 0 && !isLoading && (
        <div className="py-16 text-center">
          <p className="text-sm text-muted-foreground">
            {bots.length === 0 ? "No specialists created yet. Create your first one." : "No matching specialists found"}
          </p>
        </div>
      )}
    </div>
  );
}
