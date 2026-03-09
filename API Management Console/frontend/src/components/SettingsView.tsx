import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { getSystemStatus, updatePassword, logout } from "@/lib/api";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { Shield, Globe, Server, Loader2 } from "lucide-react";

export function SettingsView() {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
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

  return (
    <div className="max-w-2xl">
      <h1 className="font-mono text-xl font-bold text-foreground mb-1">Settings</h1>
      <p className="font-mono text-xs text-muted-foreground mb-6">Platform configuration</p>

      <div className="space-y-5">
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Shield className="w-4 h-4 text-primary" />
            <label className="font-mono text-xs font-semibold text-foreground">Admin Password</label>
          </div>
          <div className="space-y-2">
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              placeholder="Current password..."
              className="w-full bg-secondary border border-border rounded-md px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors"
            />
            <div className="flex gap-2">
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="New password..."
                className="flex-1 bg-secondary border border-border rounded-md px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors"
              />
              <button 
                onClick={() => passwordMutation.mutate({ oldPwd: oldPassword, newPwd: newPassword })}
                disabled={!oldPassword || !newPassword || passwordMutation.isPending}
                className="font-mono text-xs px-3 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-2"
              >
                {passwordMutation.isPending && <Loader2 className="w-3 h-3 animate-spin" />}
                Update
              </button>
            </div>
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Globe className="w-4 h-4 text-accent" />
            <label className="font-mono text-xs font-semibold text-foreground">Public Access</label>
          </div>
          <p className="font-mono text-[10px] text-muted-foreground mb-3">
            Use a static tunnel URL for HubSpot modules. Recommended: Cloudflare named tunnel or ngrok reserved domain.
          </p>
          <div className="bg-secondary border border-border rounded-md p-3 space-y-2">
            <code className="font-mono text-[10px] text-primary block">.\scripts\configure_static_tunnel.ps1 -Provider cloudflare -CloudflareTunnelToken "..." -CloudflarePublicBaseUrl "https://chat.example.com"</code>
            <code className="font-mono text-[10px] text-muted-foreground block"># or</code>
            <code className="font-mono text-[10px] text-primary block">.\scripts\configure_static_tunnel.ps1 -Provider ngrok -NgrokAuthtoken "..." -NgrokStaticDomain "your.ngrok-free.app"</code>
            <code className="font-mono text-[10px] text-muted-foreground block"># then restart start_console.bat</code>
            {status?.public_base_url && (
              <code className="font-mono text-[10px] text-success block">
                Active URL: {status.public_base_url}
              </code>
            )}
            {status?.tunnel_token_required && (
              <code className="font-mono text-[10px] text-warning block">
                Tunnel token protection is enabled. Include ?token=... in hardcoded HubSpot endpoints.
              </code>
            )}
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Server className="w-4 h-4 text-success" />
            <label className="font-mono text-xs font-semibold text-foreground">System Status</label>
          </div>
          {statusLoading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="w-4 h-4 text-primary animate-spin" />
            </div>
          ) : (
            <div className="space-y-2">
              {systemItems.map((s) => (
                <div key={s.label} className="flex items-center justify-between py-1">
                  <span className="font-mono text-xs text-muted-foreground">{s.label}</span>
                  <div className="flex items-center gap-1.5">
                    <div className={`w-1.5 h-1.5 rounded-full ${s.ok ? "bg-success" : "bg-muted-foreground"}`} />
                    <span className={`font-mono text-[10px] ${s.ok ? "text-success" : "text-muted-foreground"}`}>{s.status}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Globe className="w-4 h-4 text-warning" />
            <label className="font-mono text-xs font-semibold text-foreground">HubSpot Integration</label>
          </div>
          <p className="font-mono text-[10px] text-muted-foreground mb-3">
            Embed chat widget in HubSpot pages using your static domain and token:
          </p>
          <pre className="bg-secondary border border-border rounded-md p-3 font-mono text-[10px] text-primary overflow-x-auto whitespace-pre-wrap">
{`<!-- HubSpot Custom Module -->
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
</script>`}
          </pre>
        </motion.div>
      </div>
    </div>
  );
}
