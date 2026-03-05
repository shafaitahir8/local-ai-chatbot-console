import { useState } from "react";
import { motion } from "framer-motion";
import { Bot } from "@/types/bot";
import { BotCard } from "@/components/BotCard";
import { Search, Loader2 } from "lucide-react";

interface BotListViewProps {
  bots: Bot[];
  isLoading: boolean;
  onSelectBot: (bot: Bot) => void;
  onCloneBot: (bot: Bot) => void;
  onToggleBot: (bot: Bot) => void;
}

export function BotListView({ bots, isLoading, onSelectBot, onCloneBot, onToggleBot }: BotListViewProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<string>("all");

  const filtered = bots.filter((b) => {
    const matchesSearch = b.name.toLowerCase().includes(search.toLowerCase()) || b.id.includes(search);
    const matchesFilter = filter === "all" || b.status === filter;
    return matchesSearch && matchesFilter;
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 text-primary animate-spin" />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-mono text-xl font-bold text-foreground">Chatbot Fleet</h1>
          <p className="font-mono text-xs text-muted-foreground mt-1">
            {bots.length} bots registered · {bots.filter(b => b.status === "active").length} active
          </p>
        </div>
      </div>

      <div className="flex items-center gap-3 mb-5">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search bots..."
            className="w-full bg-secondary border border-border rounded-md pl-9 pr-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 focus:glow-primary transition-all"
          />
        </div>
        <div className="flex gap-1">
          {["all", "active", "inactive", "expired"].map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`font-mono text-[10px] px-2.5 py-1.5 rounded-md border transition-all ${
                filter === f
                  ? "bg-primary/10 text-primary border-primary/30"
                  : "bg-secondary text-muted-foreground border-border hover:text-foreground"
              }`}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
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
        <div className="text-center py-16">
          <p className="font-mono text-sm text-muted-foreground">
            {bots.length === 0 ? "No bots created yet. Create your first bot!" : "No bots found"}
          </p>
        </div>
      )}
    </div>
  );
}
