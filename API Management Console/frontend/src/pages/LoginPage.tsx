import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { Terminal, Eye, EyeOff, ShieldCheck, Lock, Globe, Server } from "lucide-react";
import { getSetupStatus, login } from "@/lib/api";
import { toast } from "sonner";

function formatCheckTime(iso?: string | null) {
  if (!iso) return "Pending";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "Pending";
  return parsed.toLocaleString();
}

export function LoginPage() {
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [setupStatus, setSetupStatus] = useState<{
    requires_password_change: boolean;
    encryption_enabled: boolean;
    local_private_execution: boolean;
    current_deployment_url: string | null;
    public_tunnel_ready: boolean;
    tunnel_stable: boolean;
    last_system_check_at: string | null;
    uptime_seconds: number;
  } | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let mounted = true;
    getSetupStatus()
      .then((status) => {
        if (mounted) setSetupStatus(status);
      })
      .catch(() => {
        if (mounted) {
          setSetupStatus({
            requires_password_change: false,
            encryption_enabled: false,
            local_private_execution: true,
            current_deployment_url: "http://localhost:8000/admin",
            public_tunnel_ready: false,
            tunnel_stable: false,
            last_system_check_at: null,
            uptime_seconds: 0,
          });
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  const assuranceItems = useMemo(
    () => [
      {
        label: "Encryption",
        value: setupStatus?.encryption_enabled ? "Enabled" : "Review required",
        ok: !!setupStatus?.encryption_enabled,
        icon: ShieldCheck,
      },
      {
        label: "Execution",
        value: setupStatus?.local_private_execution ? "Local / private" : "External",
        ok: !!setupStatus?.local_private_execution,
        icon: Lock,
      },
      {
        label: "System Check",
        value: formatCheckTime(setupStatus?.last_system_check_at),
        ok: !!setupStatus?.last_system_check_at,
        icon: Server,
      },
      {
        label: "Deployment URL",
        value: setupStatus?.current_deployment_url || "http://localhost:8000/admin",
        ok: true,
        icon: Globe,
      },
    ],
    [setupStatus]
  );

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const result = await login(password);
      if (result.requires_password_change) {
        sessionStorage.setItem("force_password_setup", "1");
        toast.warning("Update the default admin password before continuing.");
        navigate("/", { state: { initialView: "settings", forcePasswordSetup: true } });
      } else {
        sessionStorage.removeItem("force_password_setup");
        toast.success("Welcome to Arctic Studio");
        navigate("/");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background p-4">
      <div className="mx-auto flex min-h-screen max-w-6xl items-center justify-center">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="grid w-full max-w-5xl overflow-hidden rounded-[1.5rem] border border-border bg-card surface-shadow lg:grid-cols-[1.1fr_0.9fr]"
        >
          <div className="canvas-grid border-b border-border p-8 lg:border-b-0 lg:border-r lg:p-10">
            <div className="mb-10 flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-primary/20 bg-primary/10">
                <Terminal className="h-5 w-5 text-primary" />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-foreground">Arctic Studio</h1>
                <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-muted-foreground">HubSpot AI Operations</p>
              </div>
            </div>

            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-primary">Premium HubSpot Specialist</p>
            <h2 className="mt-3 text-3xl font-semibold leading-tight text-foreground">Private AI for CMS modules, HubL, themes, and RevOps workflows.</h2>
            <p className="mt-4 max-w-xl text-sm leading-6 text-slate-600">
              Give clients a polished, enterprise-safe AI portal for HubSpot delivery without exposing internal operator noise. Secure,
              private, and ready for production support conversations.
            </p>

            <div className="mt-8 grid gap-3 sm:grid-cols-2">
              {[
                "Module audits and HubL guidance",
                "Theme, CMS, and content support",
                "Private deployment with encrypted provider keys",
                "Knowledge-backed answers for live client work",
              ].map((item) => (
                <div key={item} className="rounded-2xl border border-border bg-white/85 px-4 py-3 text-sm text-foreground surface-shadow">
                  {item}
                </div>
              ))}
            </div>
          </div>

          <div className="p-8 lg:p-10">
            <div className="rounded-2xl border border-border bg-secondary/40 p-5">
              <div className="mb-4">
                <h3 className="text-lg font-semibold text-foreground">Admin access</h3>
                <p className="mt-1 text-xs text-muted-foreground">Sign in to manage specialists, knowledge, and HubSpot-ready endpoints.</p>
              </div>

              <form onSubmit={handleLogin} className="space-y-4">
                <div>
                    <label className="mb-2 block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Admin Password</label>
                    <div className="relative">
                    <input
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Enter your admin password"
                      className="w-full rounded-xl border border-border bg-background px-3 py-2.5 pr-10 text-sm text-foreground placeholder:text-muted-foreground transition-all focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/10"
                      autoFocus
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={loading || !password}
                  className="w-full rounded-xl bg-primary py-2.5 text-sm font-medium text-primary-foreground transition-all hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {loading ? "Authenticating..." : "Access Portal"}
                </button>
              </form>

              {setupStatus?.requires_password_change && (
                <div className="mt-4 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                  First-run security setup is still pending. After login, you will be taken directly to password rotation.
                </div>
              )}
            </div>

            <div className="mt-5 rounded-2xl border border-border bg-card p-5 surface-shadow">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-primary">Assurance</p>
                  <h3 className="mt-1 text-base font-semibold text-foreground">Live deployment posture</h3>
                </div>
              </div>
              <div className="space-y-3">
                {assuranceItems.map((item) => (
                  <div key={item.label} className="flex items-start justify-between gap-3 rounded-xl border border-border bg-secondary/30 px-3 py-3">
                    <div className="flex items-center gap-2">
                      <item.icon className={`mt-0.5 h-4 w-4 ${item.ok ? "text-primary" : "text-warning"}`} />
                      <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">{item.label}</span>
                    </div>
                    <span className={`text-right text-xs ${item.ok ? "text-foreground" : "text-warning"}`}>{item.value}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
