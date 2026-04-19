import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { getSystemStatus, updatePassword, logout } from "@/lib/api";
import { applyTheme, getStoredTheme, ThemePreference } from "@/lib/theme";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { Shield, Globe, Server, Loader2, Copy, Moon, Sun } from "lucide-react";

interface SettingsViewProps {
  forcePasswordSetup?: boolean;
}

export function SettingsView({ forcePasswordSetup = false }: SettingsViewProps) {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [theme, setTheme] = useState<ThemePreference>(() => getStoredTheme());
  const navigate = useNavigate();

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ["system-status"],
    queryFn: getSystemStatus,
    retry: false,
  });

  const passwordMutation = useMutation({
    mutationFn: ({ oldPwd, newPwd }: { oldPwd: string; newPwd: string }) => updatePassword(oldPwd, newPwd),
    onSuccess: () => {
      toast.success("Password updated. Please login again.");
      logout();
      navigate("/login");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Failed to update password");
    },
  });

  const systemItems = [
    { label: "FastAPI Server", status: status?.server || "Unknown", ok: status?.server === "Running" },
    { label: "SQLite Database", status: status?.database || "Unknown", ok: status?.database === "Connected" },
    { label: "FAISS Index", status: status?.faiss || "Unknown", ok: status?.faiss === "Loaded" },
    { label: "OpenAI API", status: status?.openai || "Not configured", ok: status?.openai === "Configured" },
    { label: "Local LLM", status: status?.local_llm || "Not configured", ok: status?.local_llm === "Configured" || status?.local_llm === "Running" },
    {
      label: "API Gateway",
      status: status?.api_gateway_running ? `Running (:${status?.api_gateway_port || "?"})` : "Unavailable",
      ok: !!status?.api_gateway_running,
    },
    {
      label: "Public Tunnel",
      status: status?.cloudflare_tunnel || "Unavailable",
      ok: status?.cloudflare_tunnel === "Running",
    },
  ];

  const copyToClipboard = async (text: string, label: string) => {
    await navigator.clipboard.writeText(text);
    toast.success(`${label} copied`);
  };

  const handleThemeToggle = () => {
    const nextTheme: ThemePreference = theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
    applyTheme(nextTheme);
    toast.success(`${nextTheme === "dark" ? "Dark" : "Light"} mode enabled`);
  };

  const cloudflareCommand = '.\\scripts\\configure_static_tunnel.ps1 -Provider cloudflare -CloudflareTunnelToken "..." -CloudflarePublicBaseUrl "https://chat.example.com"';
  const ngrokCommand = '.\\scripts\\configure_static_tunnel.ps1 -Provider ngrok -NgrokAuthtoken "..." -NgrokStaticDomain "your.ngrok-free.app"';
  const hubspotSnippet = `<!-- HubSpot Custom Module -->
<div id="ai-chat-widget"></div>
<script>
  (function() {
    var iframe = document.createElement('iframe');
    iframe.src = 'https://YOUR-STATIC-DOMAIN/embed/BOT_ID?token=YOUR_TUNNEL_SHARED_SECRET';
    iframe.style.width = '100%';
    iframe.style.height = '600px';
    iframe.style.border = 'none';
    document.getElementById('ai-chat-widget')
      .appendChild(iframe);
  })();
</script>`;

  return (
    <div className="max-w-4xl">
      <div className="mb-6 rounded-[1.25rem] border border-border bg-card p-5 surface-shadow">
        <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Business Summary</p>
        <h1 className="mt-2 text-2xl font-semibold text-foreground">Security and deployment posture</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Manage password rotation, public access setup, and the current runtime posture from one executive-friendly view.
        </p>
      </div>

      <div className="space-y-5">
        {forcePasswordSetup && (
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="rounded-[1rem] border border-warning/30 bg-warning/10 p-4">
            <div className="mb-2 flex items-center gap-2">
              <Shield className="h-4 w-4 text-warning" />
              <label className="text-sm font-semibold text-warning">Password Update Required</label>
            </div>
            <p className="text-sm text-warning">
              This deployment is still using the default admin credential. Set a new password before continuing with the rest of the portal.
            </p>
          </motion.div>
        )}

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <div className="mb-3 flex items-center gap-2">
            <Shield className="h-4 w-4 text-primary" />
            <label className="text-sm font-semibold text-foreground">Admin Password</label>
          </div>
          <div className="space-y-2">
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              placeholder="Current password..."
              className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
            />
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="New password..."
                className="flex-1 rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
              />
              <button
                onClick={() => passwordMutation.mutate({ oldPwd: oldPassword, newPwd: newPassword })}
                disabled={!oldPassword || !newPassword || passwordMutation.isPending}
                className="flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {passwordMutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                Update
              </button>
            </div>
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.03 }} className="rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <div className="mb-3 flex items-center gap-2">
            {theme === "dark" ? <Moon className="h-4 w-4 text-primary" /> : <Sun className="h-4 w-4 text-primary" />}
            <label className="text-sm font-semibold text-foreground">Appearance</label>
          </div>
          <div className="flex items-center justify-between rounded-2xl border border-border bg-secondary/30 px-4 py-3">
            <div>
              <p className="text-sm text-foreground">Dark mode</p>
              <p className="mt-1 text-xs text-muted-foreground">Switch the admin workspace between Arctic Canvas light and dark surfaces.</p>
            </div>
            <button
              onClick={handleThemeToggle}
              className="flex items-center gap-2 rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground transition-colors hover:border-primary/40"
            >
              {theme === "dark" ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
              {theme === "dark" ? "On" : "Off"}
            </button>
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <div className="mb-3 flex items-center gap-2">
            <Globe className="h-4 w-4 text-accent" />
            <label className="text-sm font-semibold text-foreground">Public Access</label>
          </div>
          <p className="mb-3 text-sm leading-6 text-muted-foreground">
            Use a stable tunnel URL for HubSpot modules. Recommended: Cloudflare named tunnel or ngrok reserved domain.
          </p>
          <div className="space-y-2 rounded-2xl border border-border bg-secondary/35 p-4">
            <div className="flex items-start justify-between gap-3 rounded-xl border border-border bg-background/70 px-3 py-2">
              <code className="block font-mono text-[10px] text-primary">{cloudflareCommand}</code>
              <button onClick={() => copyToClipboard(cloudflareCommand, "Cloudflare command")} className="text-muted-foreground hover:text-foreground">
                <Copy className="h-3.5 w-3.5" />
              </button>
            </div>
            <code className="block font-mono text-[10px] text-muted-foreground"># or</code>
            <div className="flex items-start justify-between gap-3 rounded-xl border border-border bg-background/70 px-3 py-2">
              <code className="block font-mono text-[10px] text-primary">{ngrokCommand}</code>
              <button onClick={() => copyToClipboard(ngrokCommand, "ngrok command")} className="text-muted-foreground hover:text-foreground">
                <Copy className="h-3.5 w-3.5" />
              </button>
            </div>
            <code className="block font-mono text-[10px] text-muted-foreground"># then restart start_console.bat</code>
            {status?.public_base_url && (
              <div className="flex items-start justify-between gap-3 rounded-xl border border-success/20 bg-success/5 px-3 py-2">
                <code className="block font-mono text-[10px] text-success">
                  Active URL: {status.public_base_url}
                </code>
                <button onClick={() => copyToClipboard(status.public_base_url, "Active URL")} className="text-muted-foreground hover:text-foreground">
                  <Copy className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
            {status?.tunnel_token_required && (
              <code className="block font-mono text-[10px] text-warning">
                Tunnel token protection is enabled. Include ?token=... in hardcoded HubSpot endpoints.
              </code>
            )}
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <div className="mb-3 flex items-center gap-2">
            <Server className="h-4 w-4 text-success" />
            <div>
              <label className="text-sm font-semibold text-foreground">Technical Detail</label>
              <p className="mt-1 text-sm text-muted-foreground">Current subsystem readiness and external dependency state.</p>
            </div>
          </div>
          {statusLoading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
            </div>
          ) : (
            <div className="space-y-2">
              {systemItems.map((s) => (
                <div key={s.label} className="flex items-center justify-between rounded-2xl border border-border bg-secondary/30 px-4 py-3">
                  <span className="text-sm text-foreground">{s.label}</span>
                  <div className="flex items-center gap-1.5">
                    <div className={`h-2 w-2 rounded-full ${s.ok ? "bg-success" : "bg-muted-foreground"}`} />
                    <span className={`font-mono text-[10px] ${s.ok ? "text-success" : "text-muted-foreground"}`}>{s.status}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="rounded-[1rem] border border-border bg-card p-5 surface-shadow">
          <div className="mb-3 flex items-center gap-2">
            <Globe className="h-4 w-4 text-warning" />
            <label className="text-sm font-semibold text-foreground">HubSpot Integration</label>
          </div>
          <p className="mb-3 text-sm text-muted-foreground">
            Embed the chat widget in HubSpot pages using your static domain and token:
          </p>
          <div className="rounded-2xl border border-border bg-secondary/35 p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">CLI Command Block</span>
              <button onClick={() => copyToClipboard(hubspotSnippet, "HubSpot embed snippet")} className="text-muted-foreground hover:text-foreground">
                <Copy className="h-3.5 w-3.5" />
              </button>
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[10px] text-primary">
{hubspotSnippet}
            </pre>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
