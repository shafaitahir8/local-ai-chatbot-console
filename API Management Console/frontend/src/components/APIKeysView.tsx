import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { fetchApiKeys, revealApiKeys, AdminApiKeyRecord } from "@/lib/api";
import { toast } from "sonner";
import { Copy, Eye, EyeOff, KeyRound, Loader2, ShieldAlert } from "lucide-react";

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
      <h1 className="font-mono text-xl font-bold text-foreground mb-1">API Keys</h1>
      <p className="font-mono text-xs text-muted-foreground mb-6">
        Stored provider keys for all bots. Reveal is password-gated per session.
      </p>

      {!isUnlocked && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="bg-card border border-border rounded-lg p-4 mb-5">
          <div className="flex items-center gap-2 mb-3">
            <ShieldAlert className="w-4 h-4 text-warning" />
            <span className="font-mono text-xs text-foreground">Reveal Locked</span>
          </div>
          <div className="flex gap-2">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter admin password to unlock reveal"
              className="flex-1 bg-secondary border border-border rounded-md px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors"
            />
            <button
              onClick={() => unlockMutation.mutate(password)}
              disabled={!password || unlockMutation.isPending}
              className="font-mono text-xs px-3 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-2"
            >
              {unlockMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <KeyRound className="w-3 h-3" />}
              Unlock
            </button>
          </div>
        </motion.div>
      )}

      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="font-mono text-xs text-muted-foreground uppercase tracking-wide">
            Stored Keys ({rows.length})
          </span>
        </div>

        {isLoading ? (
          <div className="py-16 flex items-center justify-center">
            <Loader2 className="w-5 h-5 text-primary animate-spin" />
          </div>
        ) : rows.length === 0 ? (
          <div className="py-16 text-center font-mono text-xs text-muted-foreground">
            No API keys stored yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border bg-secondary/40">
                  <th className="text-left p-3 font-mono text-[10px] text-muted-foreground uppercase">Company</th>
                  <th className="text-left p-3 font-mono text-[10px] text-muted-foreground uppercase">Bot</th>
                  <th className="text-left p-3 font-mono text-[10px] text-muted-foreground uppercase">Model</th>
                  <th className="text-left p-3 font-mono text-[10px] text-muted-foreground uppercase">Key</th>
                  <th className="text-right p-3 font-mono text-[10px] text-muted-foreground uppercase">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const canShow = isUnlocked && !!row.key_value;
                  const isVisible = !!visibleRows[row.id] && canShow;
                  const shownValue = isVisible ? row.key_value : row.masked_key;

                  return (
                    <tr key={row.id} className="border-b border-border/50">
                      <td className="p-3 font-mono text-xs text-foreground">{row.company}</td>
                      <td className="p-3 font-mono text-xs text-foreground">{row.bot_name}</td>
                      <td className="p-3 font-mono text-xs text-muted-foreground">{row.model || "-"}</td>
                      <td className="p-3">
                        <code className="font-mono text-[11px] text-primary break-all">{shownValue || "Unavailable"}</code>
                        {row.decrypt_error && (
                          <div className="font-mono text-[10px] text-destructive mt-1">
                            Could not decrypt this key with current ENCRYPTION_SECRET.
                          </div>
                        )}
                      </td>
                      <td className="p-3">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => toggleRowVisibility(row.id)}
                            disabled={!canShow}
                            className="p-1.5 rounded border border-border bg-secondary text-secondary-foreground hover:text-foreground disabled:opacity-40"
                            title={canShow ? (isVisible ? "Hide key" : "Reveal key") : "Unlock first"}
                          >
                            {isVisible ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                          </button>
                          <button
                            onClick={() => copyKey(canShow ? row.key_value : null)}
                            disabled={!canShow}
                            className="p-1.5 rounded border border-border bg-secondary text-secondary-foreground hover:text-foreground disabled:opacity-40"
                            title={canShow ? "Copy key" : "Unlock first"}
                          >
                            <Copy className="w-3.5 h-3.5" />
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
