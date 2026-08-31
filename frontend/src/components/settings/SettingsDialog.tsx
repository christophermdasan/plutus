import { useEffect, useState } from "react";
import { ApiError, settings as settingsApi } from "../../lib/api";
import type { LLMStatus } from "../../lib/types";
import type { Theme } from "../../lib/hooks/useTheme";
import { Badge, Button, Dialog, Spinner } from "../ui";
import { IconAlert, IconCheck, IconMonitor, IconMoon, IconSun, IconX } from "../ui/icons";

type Section = "general" | "connection";

interface Props {
  open: boolean;
  theme: Theme;
  onClose: () => void;
  onThemeChange: (theme: Theme) => void;
}

const SECTIONS: { id: Section; label: string }[] = [
  { id: "general", label: "General" },
  { id: "connection", label: "Connection" },
];

function Row({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 py-3.5">
      <div className="min-w-0">
        <div className="text-[13px] font-medium" style={{ color: "var(--color-ink)" }}>
          {title}
        </div>
        {description && (
          <div className="mt-0.5 text-[12px] leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
            {description}
          </div>
        )}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function GeneralSection({ theme, onThemeChange }: { theme: Theme; onThemeChange: (t: Theme) => void }) {
  const options: { value: Theme; label: string; Icon: typeof IconSun }[] = [
    { value: "light", label: "Light", Icon: IconSun },
    { value: "dark", label: "Dark", Icon: IconMoon },
    { value: "system", label: "System", Icon: IconMonitor },
  ];

  return (
    <div className="divide-y" style={{ borderColor: "var(--color-border)" }}>
      <Row title="Appearance" description="System follows your device setting.">
        <div
          className="flex items-center gap-0.5 rounded-lg p-0.5"
          style={{ background: "var(--color-surface)" }}
          role="radiogroup"
          aria-label="Theme"
        >
          {options.map(({ value, label, Icon }) => (
            <button
              key={value}
              role="radio"
              aria-checked={theme === value}
              title={label}
              onClick={() => onThemeChange(value)}
              className="flex h-7 w-8 items-center justify-center rounded-md transition-colors"
              style={{
                background: theme === value ? "var(--color-raised)" : "transparent",
                color: theme === value ? "var(--color-ink)" : "var(--color-ink-faint)",
              }}
            >
              <Icon size={14} />
            </button>
          ))}
        </div>
      </Row>
      <Row
        title="Keyboard shortcuts"
        description="⌘K or Ctrl K opens search. Enter sends, Shift+Enter adds a line."
      >
        <span />
      </Row>
    </div>
  );
}

/**
 * Connection: no model picker.
 *
 * Which model answers is an operator decision made in server config, not a
 * per-session user choice. What a user does need is confidence that it's
 * working - so this shows the configuration and lets them test it.
 */
function ConnectionSection() {
  const [status, setStatus] = useState<LLMStatus | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    settingsApi.llm().then(setStatus).catch(() => setStatus(null));
  }, []);

  async function test() {
    setTesting(true);
    try {
      setStatus(await settingsApi.testLlm());
    } catch (err) {
      const rateLimited = err instanceof ApiError && err.isRateLimited;
      setStatus((s) =>
        s
          ? {
              ...s,
              ok: false,
              message: rateLimited
                ? "Usage limit reached. Wait a moment and try again."
                : err instanceof Error
                  ? err.message
                  : "Test failed.",
            }
          : s,
      );
    } finally {
      setTesting(false);
    }
  }

  if (!status) {
    return <p className="py-6 text-center text-[13px]" style={{ color: "var(--color-ink-faint)" }}>Loading…</p>;
  }

  return (
    <div className="space-y-4">
      <div className="divide-y" style={{ borderColor: "var(--color-border)" }}>
        <Row title="Status">
          {status.configured ? (
            <Badge tone="verified"><IconCheck size={10} /> Configured</Badge>
          ) : (
            <Badge tone="error">Not configured</Badge>
          )}
        </Row>
        <Row title="Model">
          <span className="tabular text-[12px]" style={{ color: "var(--color-ink-muted)" }}>
            {status.model}
          </span>
        </Row>
        <Row title="Endpoint">
          <span className="tabular text-[12px]" style={{ color: "var(--color-ink-muted)" }}>
            {new URL(status.base_url).host}
          </span>
        </Row>
      </div>

      {!status.configured && (
        <div
          className="rounded-lg px-3 py-2.5 text-[12px] leading-relaxed"
          style={{ background: "var(--color-warn-soft)", color: "var(--color-warn)" }}
        >
          No API key is set. Add <span className="tabular">LLM_API_KEY</span> to the backend{" "}
          <span className="tabular">.env</span> and restart it. A free key from console.groq.com works.
        </div>
      )}

      {status.ok !== null && status.ok !== undefined && (
        <div
          className="flex items-start gap-2 rounded-lg px-3 py-2.5 text-[12px] leading-relaxed"
          style={{
            background: status.ok ? "var(--color-verified-soft)" : "var(--color-error-soft)",
            color: status.ok ? "var(--color-verified)" : "var(--color-error)",
          }}
        >
          {status.ok ? <IconCheck size={13} className="mt-0.5 shrink-0" /> : <IconAlert size={13} className="mt-0.5 shrink-0" />}
          <span>
            {status.message}
            {status.latency_ms != null && ` (${status.latency_ms}ms)`}
          </span>
        </div>
      )}

      <div>
        <Button onClick={test} disabled={testing} variant="primary">
          {testing ? <><Spinner /> Testing…</> : "Test connection"}
        </Button>
        <p className="mt-2 text-[11px] leading-relaxed" style={{ color: "var(--color-ink-faint)" }}>
          Asks the model a real question with a known answer and checks it responds correctly —
          not just that the server is reachable.
        </p>
      </div>
    </div>
  );
}

export function SettingsDialog({ open, theme, onClose, onThemeChange }: Props) {
  const [section, setSection] = useState<Section>("general");

  return (
    <Dialog open={open} onClose={onClose} width="max-w-2xl">
      <div className="flex items-center justify-between border-b px-5 py-4" style={{ borderColor: "var(--color-border)" }}>
        <h2 className="text-[15px] font-semibold" style={{ color: "var(--color-ink)" }}>
          Settings
        </h2>
        <button onClick={onClose} aria-label="Close settings" style={{ color: "var(--color-ink-muted)" }}>
          <IconX />
        </button>
      </div>

      <div className="flex min-h-[380px]">
        <nav className="w-40 shrink-0 border-r p-2" style={{ borderColor: "var(--color-border)" }}>
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => setSection(s.id)}
              className="mb-0.5 block w-full rounded-lg px-3 py-2 text-left text-[13px] transition-colors"
              style={{
                background: section === s.id ? "var(--color-surface)" : "transparent",
                color: section === s.id ? "var(--color-ink)" : "var(--color-ink-muted)",
                fontWeight: section === s.id ? 500 : 400,
              }}
            >
              {s.label}
            </button>
          ))}
        </nav>

        <div className="min-w-0 flex-1 p-5">
          {section === "general" && <GeneralSection theme={theme} onThemeChange={onThemeChange} />}
          {section === "connection" && <ConnectionSection />}
        </div>
      </div>
    </Dialog>
  );
}
