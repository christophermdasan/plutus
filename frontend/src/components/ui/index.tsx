import { useEffect, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";

/* Small primitives shared across the app. Kept together because each is a
   few lines; splitting them into files would be more ceremony than help. */

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary: "text-white border-transparent",
  secondary: "border",
  ghost: "border-transparent bg-transparent",
  danger: "border-transparent",
};

const SIZES: Record<Size, string> = {
  sm: "px-2.5 py-1.5 text-xs gap-1.5",
  md: "px-3.5 py-2 text-sm gap-2",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  style,
  ...props
}: ButtonProps) {
  const palette: Record<Variant, React.CSSProperties> = {
    primary: { background: "var(--color-accent)" },
    secondary: {
      background: "var(--color-raised)",
      borderColor: "var(--color-border-strong)",
      color: "var(--color-ink)",
    },
    ghost: { color: "var(--color-ink-muted)" },
    danger: { background: "var(--color-error)", color: "white" },
  };

  return (
    <button
      className={`inline-flex items-center justify-center rounded-lg font-medium transition-colors
        disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      style={{ ...palette[variant], ...style }}
      {...props}
    />
  );
}

export function IconButton({
  label,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      title={label}
      aria-label={label}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg
        transition-colors hover:bg-[var(--color-hover)] disabled:opacity-40 ${className}`}
      style={{ color: "var(--color-ink-muted)" }}
      {...props}
    />
  );
}

export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full rounded-lg border px-3 py-2 text-sm transition-colors
        placeholder:text-[var(--color-ink-faint)] focus:outline-none
        focus:border-[var(--color-accent)] ${className}`}
      style={{
        background: "var(--color-raised)",
        borderColor: "var(--color-border-strong)",
        color: "var(--color-ink)",
      }}
      {...props}
    />
  );
}

export function Label({ children }: { children: ReactNode }) {
  return (
    <label className="mb-1.5 block text-xs font-medium" style={{ color: "var(--color-ink-muted)" }}>
      {children}
    </label>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} width="14" height="14" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function ThinkingDots() {
  return (
    <span className="inline-flex gap-1" aria-label="Thinking">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="dot h-1.5 w-1.5 rounded-full"
          style={{ background: "var(--color-ink-faint)" }}
        />
      ))}
    </span>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "verified" | "warn" | "error" | "accent";
}) {
  const tones = {
    neutral: { background: "var(--color-surface)", color: "var(--color-ink-muted)" },
    verified: { background: "var(--color-verified-soft)", color: "var(--color-verified)" },
    warn: { background: "var(--color-warn-soft)", color: "var(--color-warn)" },
    error: { background: "var(--color-error-soft)", color: "var(--color-error)" },
    accent: { background: "var(--color-accent-soft)", color: "var(--color-accent-ink)" },
  } as const;

  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={tones[tone]}
    >
      {children}
    </span>
  );
}

export function Dialog({
  open,
  onClose,
  children,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}) {
  // Escape should always close a modal - people expect it and reach for it
  // before hunting for a close button.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fade-in fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(20,19,17,0.45)" }}
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        className={`rise max-h-[85vh] w-full ${width} overflow-y-auto rounded-2xl shadow-2xl`}
        style={{ background: "var(--color-canvas)", border: "1px solid var(--color-border)" }}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

export function EmptyHint({ children }: { children: ReactNode }) {
  return (
    <p
      className="px-4 py-8 text-center text-xs leading-relaxed"
      style={{ color: "var(--color-ink-faint)" }}
    >
      {children}
    </p>
  );
}

export function formatBytes(bytes: number | null): string {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/**
 * The grab strip between two panels.
 *
 * Rendered 1px wide but padded out to a 9px hit area, because a 1px target is
 * genuinely hard to grab. It only paints while hovered or dragging, so the
 * layout still reads as a clean divider at rest.
 */
export function ResizeHandle({
  onPointerDown,
  active,
  side,
}: {
  onPointerDown: (e: React.PointerEvent) => void;
  active: boolean;
  side: "left" | "right";
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize panel"
      onPointerDown={onPointerDown}
      className={`absolute top-0 z-30 h-full w-[9px] cursor-col-resize ${
        side === "right" ? "-right-[4px]" : "-left-[4px]"
      }`}
    >
      <div
        className="mx-auto h-full w-[1px] transition-colors"
        style={{ background: active ? "var(--color-accent)" : "transparent" }}
      />
    </div>
  );
}
