import { motion } from "framer-motion";
import { Bot, BarChart3, Plus, Settings, Terminal, LogOut, KeyRound } from "lucide-react";
import { cn } from "@/lib/utils";
import { logout } from "@/lib/api";
import { useNavigate } from "react-router-dom";

interface SidebarNavProps {
  activeView: string;
  onViewChange: (view: string) => void;
}

const navItems = [
  { id: "bots", label: "Bots", icon: Bot },
  { id: "analytics", label: "Analytics", icon: BarChart3 },
  { id: "create", label: "New Bot", icon: Plus },
  { id: "api-keys", label: "API Keys", icon: KeyRound },
  { id: "settings", label: "Settings", icon: Settings },
];

export function SidebarNav({ activeView, onViewChange }: SidebarNavProps) {
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <aside className="fixed left-0 top-0 z-50 flex h-full w-16 flex-col border-r border-border bg-card/95 backdrop-blur lg:w-56">
      <div className="p-4 flex items-center gap-3 border-b border-border">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-primary/20 bg-primary/10">
          <Terminal className="w-4 h-4 text-primary" />
        </div>
        <div className="hidden lg:block">
          <div className="text-sm font-semibold tracking-tight text-foreground">Arctic Studio</div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">HubSpot Ops</div>
        </div>
      </div>

      <nav className="flex-1 p-2 space-y-1 mt-2">
        {navItems.map((item) => {
          const isActive = activeView === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onViewChange(item.id)}
              className={cn(
                "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all duration-200",
                isActive
                  ? "border border-primary/20 bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground"
              )}
            >
              <item.icon className="w-4 h-4 shrink-0" />
              <span className="hidden lg:block truncate">{item.label}</span>
              {isActive && (
                <motion.div
                  layoutId="nav-indicator"
                  className="hidden lg:block ml-auto w-1.5 h-1.5 rounded-full bg-primary animate-pulse-glow"
                />
              )}
            </button>
          );
        })}
      </nav>

      <div className="p-3 border-t border-border space-y-2">
        <div className="flex items-center gap-2 px-2">
          <div className="w-2 h-2 rounded-full bg-success animate-pulse-glow" />
          <span className="hidden lg:block font-mono text-xs text-muted-foreground">
            localhost:8000
          </span>
        </div>
        <button
          onClick={handleLogout}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm text-muted-foreground transition-all hover:bg-secondary hover:text-foreground"
        >
          <LogOut className="w-4 h-4 shrink-0" />
          <span className="hidden lg:block truncate">Logout</span>
        </button>
      </div>
    </aside>
  );
}
