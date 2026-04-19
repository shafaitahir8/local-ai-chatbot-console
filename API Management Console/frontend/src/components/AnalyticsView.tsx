import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { fetchAnalytics } from "@/lib/api";
import { MessageSquare, Zap, Clock, Bot as BotIcon, TrendingUp, Loader2, FileText } from "lucide-react";

export function AnalyticsView() {
  const { data: analytics, isLoading } = useQuery({
    queryKey: ["analytics"],
    queryFn: () => fetchAnalytics(),
  });

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
      </div>
    );
  }

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
      <div className="mb-6 rounded-[1.25rem] border border-border bg-card p-5 surface-shadow">
        <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Business Summary</p>
        <h1 className="mt-2 text-2xl font-semibold text-foreground">Usage and delivery performance</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          A clean client-safe view of adoption, spend, and response quality across all active specialists.
        </p>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((s, i) => (
          <motion.div
            key={s.label}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="rounded-[1rem] border border-border bg-card p-4 surface-shadow"
          >
            <div className="mb-2 flex items-center gap-2">
              <s.icon className={`h-4 w-4 ${s.color}`} />
              <span className="font-mono text-[10px] uppercase text-muted-foreground">{s.label}</span>
            </div>
            <p className="text-2xl font-semibold text-foreground">{s.value}</p>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className="rounded-[1rem] border border-border bg-card p-4 surface-shadow">
          <div className="mb-4 flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-primary" />
            <span className="font-mono text-xs uppercase text-muted-foreground">Messages / Day</span>
          </div>
          {dailyUsage.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={dailyUsage}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(214 24% 88%)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fontFamily: "IBM Plex Mono", fill: "hsl(215 16% 36%)" }} />
                <YAxis tick={{ fontSize: 10, fontFamily: "IBM Plex Mono", fill: "hsl(215 16% 36%)" }} />
                <Tooltip
                  contentStyle={{
                    background: "hsl(0 0% 100%)",
                    border: "1px solid hsl(214 24% 88%)",
                    borderRadius: 12,
                    fontFamily: "IBM Plex Sans",
                    fontSize: 12,
                    boxShadow: "0 14px 40px hsl(220 30% 20% / 0.12)",
                  }}
                  labelStyle={{ color: "hsl(222 47% 11%)" }}
                />
                <Bar dataKey="messages" fill="hsl(224 76% 48%)" radius={[8, 8, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
              No data available
            </div>
          )}
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }} className="rounded-[1rem] border border-border bg-card p-4 surface-shadow">
          <div className="mb-4 flex items-center gap-2">
            <Zap className="h-4 w-4 text-warning" />
            <span className="font-mono text-xs uppercase text-muted-foreground">Token Usage / Day</span>
          </div>
          {dailyUsage.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={dailyUsage}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(214 24% 88%)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fontFamily: "IBM Plex Mono", fill: "hsl(215 16% 36%)" }} />
                <YAxis tick={{ fontSize: 10, fontFamily: "IBM Plex Mono", fill: "hsl(215 16% 36%)" }} />
                <Tooltip
                  contentStyle={{
                    background: "hsl(0 0% 100%)",
                    border: "1px solid hsl(214 24% 88%)",
                    borderRadius: 12,
                    fontFamily: "IBM Plex Sans",
                    fontSize: 12,
                    boxShadow: "0 14px 40px hsl(220 30% 20% / 0.12)",
                  }}
                  labelStyle={{ color: "hsl(222 47% 11%)" }}
                />
                <Line type="monotone" dataKey="tokens" stroke="hsl(27 91% 37%)" strokeWidth={2} dot={{ fill: "hsl(27 91% 37%)", r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
              No data available
            </div>
          )}
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="lg:col-span-2 rounded-[1rem] border border-border bg-card p-4 surface-shadow">
          <div className="mb-3 flex items-center gap-2">
            <FileText className="h-4 w-4 text-accent" />
            <div>
              <span className="font-mono text-xs uppercase text-primary">Technical Detail</span>
              <p className="mt-1 text-sm text-muted-foreground">Frequently asked questions and repeated prompts across the platform.</p>
            </div>
          </div>
          {topQuestions.length > 0 ? (
            <div className="space-y-2">
              {topQuestions.map((q, i) => (
                <div key={i} className="flex items-center justify-between rounded-2xl border border-border bg-secondary/30 px-4 py-3">
                  <span className="text-sm text-foreground">{q.question}</span>
                  <span className="font-mono text-xs text-muted-foreground">{q.count}x</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-8 text-center text-sm text-muted-foreground">
              No questions recorded yet
            </div>
          )}
        </motion.div>
      </div>
    </div>
  );
}
