import { motion } from "framer-motion";
import { Bot as BotIcon, Zap, Clock, MessageSquare, Copy, ToggleLeft, ToggleRight } from "lucide-react";
import { Bot, BotStatus } from "@/types/bot";
import { cn } from "@/lib/utils";

interface BotCardProps {
  bot: Bot;
  index: number;
  onSelect: (bot: Bot) => void;
  onClone: (bot: Bot) => void;
  onToggle: (bot: Bot) => void;
}

const statusConfig: Record<BotStatus, { label: string; className: string }> = {
  active: { label: "ACTIVE", className: "bg-success/10 text-success border-success/30" },
  inactive: { label: "INACTIVE", className: "bg-muted text-muted-foreground border-border" },
  expired: { label: "EXPIRED", className: "bg-destructive/10 text-destructive border-destructive/30" },
};

export function BotCard({ bot, index, onSelect, onClone, onToggle }: BotCardProps) {
  const status = statusConfig[bot.status];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05, duration: 0.3 }}
      className="group cursor-pointer rounded-[1rem] border border-border bg-card p-5 surface-shadow transition-all duration-300 hover:-translate-y-0.5 hover:border-primary/25"
      onClick={() => onSelect(bot)}
    >
      <div className="mb-4 flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-secondary">
            <BotIcon className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-foreground">{bot.name}</h3>
            <span className="font-mono text-[10px] text-muted-foreground">{bot.id.slice(0, 8)}...</span>
          </div>
        </div>
        <span className={cn("rounded-full border px-2.5 py-1 font-mono text-[10px]", status.className)}>
          {status.label}
        </span>
      </div>

      <div className="mb-4 grid grid-cols-3 gap-3">
        <div className="rounded-2xl border border-border bg-secondary/35 p-3">
          <div className="mb-1 flex items-center gap-1 text-muted-foreground">
            <MessageSquare className="h-3 w-3" />
            <span className="font-mono text-[10px]">MSGS</span>
          </div>
          <p className="text-sm font-semibold text-foreground">{(bot.messages_used || 0).toLocaleString()}</p>
        </div>
        <div className="rounded-2xl border border-border bg-secondary/35 p-3">
          <div className="mb-1 flex items-center gap-1 text-muted-foreground">
            <Zap className="h-3 w-3" />
            <span className="font-mono text-[10px]">TOKENS</span>
          </div>
          <p className="text-sm font-semibold text-foreground">{((bot.token_usage || 0) / 1000).toFixed(1)}k</p>
        </div>
        <div className="rounded-2xl border border-border bg-secondary/35 p-3">
          <div className="mb-1 flex items-center gap-1 text-muted-foreground">
            <Clock className="h-3 w-3" />
            <span className="font-mono text-[10px]">LATENCY</span>
          </div>
          <p className="text-sm font-semibold text-foreground">{(bot.avg_latency || 0).toFixed(1)}s</p>
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-border pt-3">
        <div className="flex items-center gap-1.5">
          <span className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[10px] text-secondary-foreground">
            {(bot.provider_type || "openai").toUpperCase()}
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            t={bot.temperature}
          </span>
          {bot.memory_enabled && (
            <span className="rounded-full border border-accent/20 bg-accent/10 px-1.5 py-0.5 font-mono text-[10px] text-accent">
              MEM
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <button
            onClick={(e) => { e.stopPropagation(); onClone(bot); }}
            className="rounded-xl p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            title="Clone Bot"
          >
            <Copy className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onToggle(bot); }}
            className="rounded-xl p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            title="Toggle Status"
          >
            {bot.status === "active" ? <ToggleRight className="h-3.5 w-3.5 text-success" /> : <ToggleLeft className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>
    </motion.div>
  );
}
