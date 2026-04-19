import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { fetchApiKeys, revealApiKeys, AdminApiKeyRecord } from "@/lib/api";
import { toast } from "sonner";
import { Copy, Eye, EyeOff, KeyRound, Loader2, ShieldAlert, ShieldCheck, Lock } from "lucide-react";

export function APIKeysView() {
  const [password, setPassword] = useState("");
  const [isUnlocked, setIsUnlocked] = useState(sessionStorage.getItem("api_keys_unlocked") === "1");
  const [visibleRows, setVisibleRows] = useState<Record<string, boolean>>({});
  const [revealedData, setRevealedData] = useState<AdminApiKeyRecord[] | null>(null);

  const { data: maskedData, isLoading } = useQuery({
    queryKey: ["admin-api-keys"],
    queryFn: fetchApiKeys,
  });

  const unlockMutation = useMutation({
    mutationFn: (pwd: string) => revealApiKeys(pwd),
    onSuccess: (data) => {
      sessionStorage.setItem("api_keys_unlocked", "1");
      setIsUnlocked(true);
      setRevealedData(data.keys || []);
      toast.success("API key reveal unlocked for this session");
      setPassword("");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Invalid admin password");
    },
  });

  const rows = useMemo(
    () => (isUnlocked && revealedData ? revealedData : (maskedData?.keys || [])),
    [isUnlocked, revealedData, maskedData]
  );

  const unlockedCount = useMemo(
    () => rows.filter((row) => row.has_key).length,
    [rows]
  );

  const toggleRowVisibility = (rowId: string) => {
    setVisibleRows((prev) => ({ ...prev, [rowId]: !prev[rowId] }));
  };

  const copyKey = async (value: string | undefined | null) => {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    toast.success("API key copied");
  };

  return (
    <div>
      <div className="mb-6 rounded-[1.25rem] border border-border bg-card p-5 surface-shadow">
        <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Business Summary</p>
        <h1 className="mt-2 text-2xl font-semibold text-foreground">Provider key custody</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
          Review encrypted provider credentials without exposing raw values by default. Sensitive reveals stay password-gated
          and limited to the current session.
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <div className="rounded-2xl border border-border bg-secondary/35 p-4">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Stored Keys</span>
            </div>
            <p className="mt-2 text-lg font-semibold text-foreground">{unlockedCount}</p>
          </div>
          <div className="rounded-2xl border border-border bg-secondary/35 p-4">
            <div className="flex items-center gap-2">
              <Lock className="h-4 w-4 text-success" />
              <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Reveal Mode</span>
            </div>
            <p className="mt-2 text-lg font-semibold text-foreground">{isUnlocked ? "Unlocked" : "Protected"}</p>
          </div>
          <div className="rounded-2xl border border-border bg-secondary/35 p-4">
            <div className="flex items-center gap-2">
              <KeyRound className="h-4 w-4 text-accent" />
              <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">Session Scope</span>
            </div>
            <p className="mt-2 text-lg font-semibold text-foreground">Admin-only</p>
          </div>
        </div>
      </div>

      {!isUnlocked && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mb-5 rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <div className="mb-3 flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-warning" />
            <span className="text-sm font-semibold text-foreground">Reveal locked</span>
          </div>
          <p className="mb-3 text-sm text-muted-foreground">
            Technical detail remains masked until the admin password is re-entered for this session.
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter admin password to unlock reveal"
              className="flex-1 rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
            />
            <button
              onClick={() => unlockMutation.mutate(password)}
              disabled={!password || unlockMutation.isPending}
              className="flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {unlockMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <KeyRound className="h-3 w-3" />}
              Unlock
            </button>
          </div>
        </motion.div>
      )}

      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="overflow-hidden rounded-[1rem] border border-border bg-card surface-shadow">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <span className="font-mono text-[10px] uppercase tracking-wide text-primary">Technical Detail</span>
            <p className="mt-1 text-sm text-muted-foreground">Stored key inventory and per-bot provider assignments.</p>
          </div>
          <span className="rounded-full border border-border bg-secondary px-2.5 py-1 font-mono text-[10px] text-muted-foreground">
            {rows.length} records
          </span>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
          </div>
        ) : rows.length === 0 ? (
          <div className="py-16 text-center text-sm text-muted-foreground">
            No API keys stored yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border bg-secondary/40">
                  <th className="p-3 text-left font-mono text-[10px] uppercase text-muted-foreground">Company</th>
                  <th className="p-3 text-left font-mono text-[10px] uppercase text-muted-foreground">Bot</th>
                  <th className="p-3 text-left font-mono text-[10px] uppercase text-muted-foreground">Model</th>
                  <th className="p-3 text-left font-mono text-[10px] uppercase text-muted-foreground">Key</th>
                  <th className="p-3 text-right font-mono text-[10px] uppercase text-muted-foreground">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const canShow = isUnlocked && !!row.key_value;
                  const isVisible = !!visibleRows[row.id] && canShow;
                  const shownValue = isVisible ? row.key_value : row.masked_key;

                  return (
                    <tr key={row.id} className="border-b border-border/50 align-top">
                      <td className="p-3 text-sm text-foreground">{row.company}</td>
                      <td className="p-3 text-sm text-foreground">{row.bot_name}</td>
                      <td className="p-3 text-sm text-muted-foreground">{row.model || "-"}</td>
                      <td className="p-3">
                        <code className="break-all font-mono text-[11px] text-primary">{shownValue || "Unavailable"}</code>
                        {row.decrypt_error && (
                          <div className="mt-1 font-mono text-[10px] text-destructive">
                            Could not decrypt this key with current ENCRYPTION_SECRET.
                          </div>
                        )}
                      </td>
                      <td className="p-3">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => toggleRowVisibility(row.id)}
                            disabled={!canShow}
                            className="rounded-xl border border-border bg-secondary p-1.5 text-secondary-foreground transition-colors hover:text-foreground disabled:opacity-40"
                            title={canShow ? (isVisible ? "Hide key" : "Reveal key") : "Unlock first"}
                          >
                            {isVisible ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                          </button>
                          <button
                            onClick={() => copyKey(canShow ? row.key_value : null)}
                            disabled={!canShow}
                            className="rounded-xl border border-border bg-secondary p-1.5 text-secondary-foreground transition-colors hover:text-foreground disabled:opacity-40"
                            title={canShow ? "Copy key" : "Unlock first"}
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </motion.div>
    </div>
  );
}
