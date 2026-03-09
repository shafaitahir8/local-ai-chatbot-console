import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { Send, Bot as BotIcon } from "lucide-react";
import { sendMessage } from "@/lib/api";

interface EmbedChatPreviewProps {
  botName: string;
  botId: string;
  accentColor?: string;
}

export function EmbedChatPreview({ botName, botId, accentColor = "#00d4aa" }: EmbedChatPreviewProps) {
  const [messages, setMessages] = useState<{ role: "user" | "bot"; text: string }[]>([
    { role: "bot", text: `Hi! I'm ${botName}. How can I help you?` },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingText, setLoadingText] = useState("Ready.");
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = async () => {
    if (!input.trim() || loading) return;
    const userMsg = input;
    setMessages((prev) => [...prev, { role: "user", text: userMsg }]);
    setInput("");
    setLoading(true);
    setLoadingText(sessionId ? "Preparing request..." : "Opening a new chat session...");

    try {
      const response = await sendMessage(botId, userMsg, sessionId, {
        maxRetries: 2,
        onProgress: (update) => {
          setLoadingText(update.message);
        },
      });
      setMessages((prev) => [...prev, { role: "bot", text: response.response }]);
      // Store session ID for conversation continuity
      if (response.session_id) {
        setSessionId(response.session_id);
      }
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        { role: "bot", text: "Sorry, I encountered an error. Please try again." },
      ]);
      setLoadingText("Request failed.");
    } finally {
      setLoading(false);
      setLoadingText("Ready.");
    }
  };

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden w-full max-w-none shadow-lg">
      {/* Header */}
      <div className="px-5 py-4 border-b border-border flex items-center gap-3 bg-secondary/40" style={{ borderBottomColor: accentColor + "33" }}>
        <div className="w-9 h-9 rounded-full flex items-center justify-center" style={{ background: accentColor + "20" }}>
          <BotIcon className="w-4 h-4" style={{ color: accentColor }} />
        </div>
        <span className="font-mono text-sm font-semibold text-foreground">{botName}</span>
        <div className="ml-auto w-2 h-2 rounded-full animate-pulse-glow" style={{ background: accentColor }} />
      </div>

      {/* Messages */}
      <div className="h-[460px] overflow-y-auto p-4 space-y-3 scanline">
        {messages.map((msg, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            className={`flex gap-2 ${msg.role === "user" ? "justify-end" : ""}`}
          >
            {msg.role === "bot" && (
              <div className="w-5 h-5 rounded-full flex items-center justify-center shrink-0 mt-0.5" style={{ background: accentColor + "20" }}>
                <BotIcon className="w-2.5 h-2.5" style={{ color: accentColor }} />
              </div>
            )}
            <div
              className={`max-w-[75%] px-3 py-2 rounded-lg font-mono text-xs ${
                msg.role === "user"
                  ? "bg-primary/10 text-foreground"
                  : "bg-secondary text-foreground"
              }`}
            >
              {msg.text}
            </div>
          </motion.div>
        ))}
        {loading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex gap-2"
          >
            <div className="w-5 h-5 rounded-full flex items-center justify-center shrink-0 mt-0.5" style={{ background: accentColor + "20" }}>
              <BotIcon className="w-2.5 h-2.5" style={{ color: accentColor }} />
            </div>
            <div className="bg-secondary rounded-lg px-3 py-2">
              <div className="flex items-center gap-2">
                <div className="flex gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "0ms" }} />
                  <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "150ms" }} />
                  <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
                <span className="font-mono text-[10px] text-muted-foreground">{loadingText}</span>
              </div>
            </div>
          </motion.div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t border-border bg-secondary/20">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Type a message..."
            disabled={loading}
            className="flex-1 bg-secondary border border-border rounded-md px-3 py-2.5 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 transition-colors disabled:opacity-50"
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="px-4 py-2.5 rounded-md text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ background: accentColor }}
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
