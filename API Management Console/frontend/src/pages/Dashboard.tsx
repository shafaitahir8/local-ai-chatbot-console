import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { SidebarNav } from "@/components/SidebarNav";
import { BotListView } from "@/components/BotListView";
import { BotDetailView } from "@/components/BotDetailView";
import { AnalyticsView } from "@/components/AnalyticsView";
import { CreateBotView } from "@/components/CreateBotView";
import { SettingsView } from "@/components/SettingsView";
import { EmbedChatPreview } from "@/components/EmbedChatPreview";
import { fetchBots, cloneBot, toggleBot } from "@/lib/api";
import { Bot } from "@/types/bot";
import { toast } from "sonner";

export function Dashboard() {
  const [view, setView] = useState("bots");
  const [selectedBot, setSelectedBot] = useState<Bot | null>(null);
  const queryClient = useQueryClient();

  const { data: botsData, isLoading } = useQuery({
    queryKey: ["bots"],
    queryFn: fetchBots,
  });

  const bots = botsData?.bots || [];

  const cloneMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name?: string }) => cloneBot(id, name),
    onSuccess: (newBot) => {
      queryClient.invalidateQueries({ queryKey: ["bots"] });
      toast.success(`Cloned → "${newBot.name}"`);
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

  return (
    <div className="min-h-screen bg-background">
      <SidebarNav 
        activeView={view === "detail" ? "bots" : view} 
        onViewChange={(v) => { 
          setView(v); 
          setSelectedBot(null); 
        }} 
      />

      <main className="pl-16 lg:pl-[19rem] p-6 lg:p-8">
        {view === "bots" && (
          <BotListView
            bots={bots}
            isLoading={isLoading}
            onSelectBot={handleSelectBot}
            onCloneBot={handleClone}
            onToggleBot={handleToggle}
          />
        )}

        {view === "detail" && selectedBot && (
          <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-8">
            <BotDetailView
              bot={selectedBot}
              onBack={() => setView("bots")}
              onSave={handleBotSaved}
              onClone={handleClone}
            />
            <div className="hidden xl:block">
              <label className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider mb-3 block">
                Chat Widget Preview
              </label>
              <EmbedChatPreview 
                botName={selectedBot.name} 
                botId={selectedBot.id} 
                accentColor={selectedBot.accent_color} 
              />
            </div>
          </div>
        )}

        {view === "analytics" && <AnalyticsView />}
        {view === "create" && <CreateBotView onCreated={() => setView("bots")} />}
        {view === "settings" && <SettingsView />}
      </main>
    </div>
  );
}
