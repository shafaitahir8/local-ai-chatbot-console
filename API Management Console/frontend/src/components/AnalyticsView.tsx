import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { fetchAnalytics } from "@/lib/api";
import { MessageSquare, Zap, Clock, Bot as BotIcon, TrendingUp, Loader2 } from "lucide-react";

export function AnalyticsView() {
  const { data: analytics, isLoading } = useQuery({
    queryKey: ["analytics"],
    queryFn: () => fetchAnalytics(),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 text-primary animate-spin" />
      </div>
    );
  }

  // Fallback data if API doesn't return expected format
  const stats = [
    { label: "Total Messages", value: (analytics?.total_messages || 0).toLocaleString(), icon: MessageSquare, color: "text-primary" },
    { label: "Total Tokens", value: `${((analytics?.total_tokens || 0) / 1000).toFixed(0)}k`, icon: Zap, color: "text-warning" },
    { label: "Avg Latency", value: `${(analytics?.avg_latency || 0).toFixed(1)}s`, icon: Clock, color: "text-accent" },
    { label: "Active Bots", value: (analytics?.active_bots || 0).toString(), icon: BotIcon, color: "text-success" },
  ];

  const dailyUsage = analytics?.daily_usage || [];
  const topQuestions = analytics?.top_questions || [];

  return (
    <div>
      <h1 className="font-mono text-xl font-bold text-foreground mb-1">Analytics</h1>
      <p className="font-mono text-xs text-muted-foreground mb-6">Platform-wide usage metrics</p>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {stats.map((s, i) => (
          <motion.div
            key={s.label}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="bg-card border border-border rounded-lg p-4"
          >
            <div className="flex items-center gap-2 mb-2">
              <s.icon className={`w-4 h-4 ${s.color}`} />
              <span className="font-mono text-[10px] text-muted-foreground uppercase">{s.label}</span>
            </div>
            <p className="font-mono text-2xl font-bold text-foreground">{s.value}</p>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp className="w-4 h-4 text-primary" />
            <span className="font-mono text-xs text-muted-foreground uppercase">Messages / Day</span>
          </div>
          {dailyUsage.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={dailyUsage}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(220 15% 14%)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fontFamily: "JetBrains Mono", fill: "hsl(220 10% 45%)" }} />
                <YAxis tick={{ fontSize: 10, fontFamily: "JetBrains Mono", fill: "hsl(220 10% 45%)" }} />
                <Tooltip
                  contentStyle={{ background: "hsl(220 18% 7%)", border: "1px solid hsl(220 15% 14%)", borderRadius: 6, fontFamily: "JetBrains Mono", fontSize: 11 }}
                  labelStyle={{ color: "hsl(180 10% 88%)" }}
                />
                <Bar dataKey="messages" fill="hsl(170 80% 50%)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[200px] flex items-center justify-center text-muted-foreground font-mono text-xs">
              No data available
            </div>
          )}
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }} className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-4">
            <Zap className="w-4 h-4 text-warning" />
            <span className="font-mono text-xs text-muted-foreground uppercase">Token Usage / Day</span>
          </div>
          {dailyUsage.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={dailyUsage}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(220 15% 14%)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fontFamily: "JetBrains Mono", fill: "hsl(220 10% 45%)" }} />
                <YAxis tick={{ fontSize: 10, fontFamily: "JetBrains Mono", fill: "hsl(220 10% 45%)" }} />
                <Tooltip
                  contentStyle={{ background: "hsl(220 18% 7%)", border: "1px solid hsl(220 15% 14%)", borderRadius: 6, fontFamily: "JetBrains Mono", fontSize: 11 }}
                  labelStyle={{ color: "hsl(180 10% 88%)" }}
                />
                <Line type="monotone" dataKey="tokens" stroke="hsl(38 90% 55%)" strokeWidth={2} dot={{ fill: "hsl(38 90% 55%)", r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[200px] flex items-center justify-center text-muted-foreground font-mono text-xs">
              No data available
            </div>
          )}
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="lg:col-span-2 bg-card border border-border rounded-lg p-4">
          <span className="font-mono text-xs text-muted-foreground uppercase block mb-3">Top Questions</span>
          {topQuestions.length > 0 ? (
            <div className="space-y-2">
              {topQuestions.map((q, i) => (
                <div key={i} className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
                  <span className="font-mono text-xs text-foreground">{q.question}</span>
                  <span className="font-mono text-xs text-muted-foreground">{q.count}×</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground font-mono text-xs">
              No questions recorded yet
            </div>
          )}
        </motion.div>
      </div>
    </div>
  );
}
