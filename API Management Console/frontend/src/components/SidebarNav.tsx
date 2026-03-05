import { motion } from "framer-motion";
import { Bot, BarChart3, Plus, Settings, Terminal, LogOut } from "lucide-react";
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
  { id: "settings", label: "Settings", icon: Settings },
];

export function SidebarNav({ activeView, onViewChange }: SidebarNavProps) {
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <aside className="fixed left-0 top-0 h-full w-16 lg:w-56 bg-card border-r border-border flex flex-col z-50">
      <div className="p-4 flex items-center gap-3 border-b border-border">
        <div className="w-8 h-8 rounded-md bg-primary/10 border border-primary/30 flex items-center justify-center glow-primary">
          <Terminal className="w-4 h-4 text-primary" />
        </div>
        <span className="hidden lg:block font-mono text-sm font-semibold text-foreground tracking-tight">
          AI Console
        </span>
      </div>

      <nav className="flex-1 p-2 space-y-1 mt-2">
        {navItems.map((item) => {
          const isActive = activeView === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onViewChange(item.id)}
              className={cn(
                "w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-all duration-200 font-mono",
                isActive
                  ? "bg-primary/10 text-primary border border-primary/20 glow-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary"
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
          className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-mono text-muted-foreground hover:text-foreground hover:bg-secondary transition-all"
        >
          <LogOut className="w-4 h-4 shrink-0" />
          <span className="hidden lg:block truncate">Logout</span>
        </button>
      </div>
    </aside>
  );
}
