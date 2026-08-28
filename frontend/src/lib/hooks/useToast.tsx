import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Tone = "info" | "success" | "error";
interface Toast { id: number; message: string; tone: Tone }

// An error explains what went wrong and what to do about it, so it is usually
// a sentence or two - four seconds is not long enough to read one, and a
// message nobody finishes reading may as well not have been shown.
const DISMISS_MS: Record<Tone, number> = { info: 4000, success: 4000, error: 10000 };

const ToastContext = createContext<(message: string, tone?: Tone) => void>(() => {});

export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, tone: Tone = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), DISMISS_MS[tone]);
  }, []);

  const colors: Record<Tone, React.CSSProperties> = {
    info: { background: "var(--color-raised)", color: "var(--color-ink)" },
    success: { background: "var(--color-verified-soft)", color: "var(--color-verified)" },
    error: { background: "var(--color-error-soft)", color: "var(--color-error)" },
  };

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 left-1/2 z-[60] flex -translate-x-1/2 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className="rise max-w-md rounded-lg border px-3.5 py-2 text-sm leading-relaxed shadow-lg"
            style={{ ...colors[t.tone], borderColor: "var(--color-border)" }}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
