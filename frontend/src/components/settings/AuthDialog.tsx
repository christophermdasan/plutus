import { useState, type FormEvent } from "react";
import { auth as authApi, setToken } from "../../lib/api";
import type { User } from "../../lib/types";
import { Button, Dialog, Input, Spinner } from "../ui";
import { IconAlert } from "../ui/icons";

interface Props {
  open: boolean;
  onClose: () => void;
  onAuthenticated: (user: User) => void;
}

export function AuthDialog({ open, onClose, onAuthenticated }: Props) {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result =
        mode === "signin"
          ? await authApi.login(email, password)
          : await authApi.signup(email, name, password);
      setToken(result.token);
      onAuthenticated(result.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} width="max-w-sm">
      <div className="p-6">
        <div className="mb-5 flex gap-1 rounded-lg p-1" style={{ background: "var(--color-surface)" }}>
          {(["signin", "signup"] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(null); }}
              className="flex-1 rounded-md py-1.5 text-[13px] font-medium transition-colors"
              style={{
                background: mode === m ? "var(--color-raised)" : "transparent",
                color: mode === m ? "var(--color-ink)" : "var(--color-ink-muted)",
              }}
            >
              {m === "signin" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-2.5">
          {mode === "signup" && (
            <Input
              placeholder="Name" autoComplete="name" required
              value={name} onChange={(e) => setName(e.target.value)}
            />
          )}
          <Input
            type="email" placeholder="Email" autoComplete="email" required
            value={email} onChange={(e) => setEmail(e.target.value)}
          />
          <Input
            type="password" placeholder="Password" required minLength={8}
            autoComplete={mode === "signin" ? "current-password" : "new-password"}
            value={password} onChange={(e) => setPassword(e.target.value)}
          />

          {error && (
            <p className="flex items-start gap-1.5 text-[12px]" style={{ color: "var(--color-error)" }}>
              <IconAlert size={13} className="mt-0.5 shrink-0" />
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" disabled={busy} className="w-full">
            {busy ? <Spinner /> : mode === "signin" ? "Sign in" : "Create account"}
          </Button>
        </form>

        <p className="mt-4 text-center text-[11px] leading-relaxed" style={{ color: "var(--color-ink-faint)" }}>
          You don't need an account — signing in just keeps your filings and history with you across devices.
        </p>
      </div>
    </Dialog>
  );
}
