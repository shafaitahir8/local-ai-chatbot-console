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
      className="group bg-card border border-border rounded-lg p-4 hover:border-primary/30 hover:glow-primary transition-all duration-300 cursor-pointer"
      onClick={() => onSelect(bot)}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-md bg-secondary flex items-center justify-center">
            <BotIcon className="w-4 h-4 text-primary" />
          </div>
          <div>
            <h3 className="font-mono text-sm font-semibold text-foreground">{bot.name}</h3>
            <span className="font-mono text-[10px] text-muted-foreground">{bot.id.slice(0, 8)}...</span>
          </div>
        </div>
        <span className={cn("font-mono text-[10px] px-2 py-0.5 rounded border", status.className)}>
          {status.label}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3 mb-3">
        <div className="space-y-0.5">
          <div className="flex items-center gap-1 text-muted-foreground">
            <MessageSquare className="w-3 h-3" />
            <span className="font-mono text-[10px]">MSGS</span>
          </div>
          <p className="font-mono text-sm text-foreground">{(bot.messages_used || 0).toLocaleString()}</p>
        </div>
        <div className="space-y-0.5">
          <div className="flex items-center gap-1 text-muted-foreground">
            <Zap className="w-3 h-3" />
            <span className="font-mono text-[10px]">TOKENS</span>
          </div>
          <p className="font-mono text-sm text-foreground">{((bot.token_usage || 0) / 1000).toFixed(1)}k</p>
        </div>
        <div className="space-y-0.5">
          <div className="flex items-center gap-1 text-muted-foreground">
            <Clock className="w-3 h-3" />
            <span className="font-mono text-[10px]">LATENCY</span>
          </div>
          <p className="font-mono text-sm text-foreground">{(bot.avg_latency || 0).toFixed(1)}s</p>
        </div>
      </div>

      <div className="flex items-center justify-between pt-3 border-t border-border">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground">
            {(bot.provider_type || "openai").toUpperCase()}
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            t={bot.temperature}
          </span>
          {bot.memory_enabled && (
            <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
              MEM
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e) => { e.stopPropagation(); onClone(bot); }}
            className="p-1.5 rounded hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors"
            title="Clone Bot"
          >
            <Copy className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onToggle(bot); }}
            className="p-1.5 rounded hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors"
            title="Toggle Status"
          >
            {bot.status === "active" ? <ToggleRight className="w-3.5 h-3.5 text-success" /> : <ToggleLeft className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>
    </motion.div>
  );
}
