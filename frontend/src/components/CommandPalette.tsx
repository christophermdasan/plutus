import { useEffect, useMemo, useRef, useState } from "react";
import { chat as chatApi } from "../lib/api";
import type { Filing, Message } from "../lib/types";
import { IconFile, IconSearch, IconSearchOff } from "./ui/icons";

interface Props {
  open: boolean;
  filings: Filing[];
  onClose: () => void;
  onSelectFiling: (id: string) => void;
  onOpenAnswer: (message: Message) => void;
}

/**
 * ⌘K: jump to a filing, or find something you asked before.
 *
 * Past answers are searched server-side across the whole workspace, so a
 * half-remembered question ("what did it say about impairment?") gets you
 * back to the answer *and* its citation.
 */
export function CommandPalette({ open, filings, onClose, onSelectFiling, onOpenAnswer }: Props) {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setMessages([]);
      const timer = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(timer);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // Debounced so typing doesn't fire a request per keystroke.
  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setMessages([]);
      return;
    }
    let active = true;
    const timer = setTimeout(() => {
      chatApi
        .search(query.trim())
        .then((results) => active && setMessages(results))
        .catch(() => active && setMessages([]));
    }, 220);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [query, open]);

  const matchingFilings = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return filings.slice(0, 6);
    return filings
      .filter(
        (f) =>
          f.display_title.toLowerCase().includes(q) ||
          f.original_name.toLowerCase().includes(q),
      )
      .slice(0, 6);
  }, [filings, query]);

  if (!open) return null;

  const nothing = matchingFilings.length === 0 && messages.length === 0 && query.trim().length >= 2;

  // `page` is the viewer's sequential page index.  For filings with
  // unnumbered front matter, `label` is the page number printed in the filing
  // and is the number an analyst uses to verify the evidence by hand.
  function pageReference(message: Message) {
    if (!message.page) return null;
    const printedPage = message.citations?.find((citation) => citation.page === message.page)?.label;
    if (printedPage == null || printedPage === message.page) return `p. ${message.page}`;
    return `p. ${printedPage} (viewer ${message.page})`;
  }

  return (
    <div
      className="fade-in fixed inset-0 z-50 flex items-start justify-center p-4 pt-[12vh]"
      style={{ background: "rgba(20,19,17,0.45)" }}
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        className="rise w-full max-w-xl overflow-hidden rounded-2xl border shadow-2xl"
        style={{ background: "var(--color-canvas)", borderColor: "var(--color-border)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center gap-2.5 border-b px-4 py-3"
          style={{ borderColor: "var(--color-border)" }}
        >
          <IconSearch className="shrink-0 text-[var(--color-ink-faint)]" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search filings and past answers…"
            className="flex-1 bg-transparent text-[15px] placeholder:text-[var(--color-ink-faint)] focus:outline-none"
            style={{ color: "var(--color-ink)" }}
          />
          <kbd className="tabular rounded px-1.5 py-0.5 text-[10px]" style={{ background: "var(--color-surface)", color: "var(--color-ink-faint)" }}>
            ESC
          </kbd>
        </div>

        <div className="max-h-[52vh] overflow-y-auto p-2">
          {matchingFilings.length > 0 && (
            <>
              <div className="px-2 py-1.5 text-[11px] font-medium uppercase tracking-wide" style={{ color: "var(--color-ink-faint)" }}>
                Filings
              </div>
              {matchingFilings.map((f) => (
                <button
                  key={f.id}
                  onClick={() => { onSelectFiling(f.id); onClose(); }}
                  disabled={f.status !== "ready"}
                  className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors
                    hover:bg-[var(--color-hover)] disabled:opacity-50"
                >
                  <IconFile className="shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px]" style={{ color: "var(--color-ink)" }}>
                      {f.display_title}
                    </span>
                  </span>
                </button>
              ))}
            </>
          )}

          {messages.length > 0 && (
            <>
              <div className="mt-2 px-2 py-1.5 text-[11px] font-medium uppercase tracking-wide" style={{ color: "var(--color-ink-faint)" }}>
                Past answers
              </div>
              {messages.map((m) => {
                const reference = pageReference(m);
                return (
                    <button
                      key={m.id}
                      onClick={() => { onOpenAnswer(m); onClose(); }}
                      className="w-full rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-[var(--color-hover)]"
                    >
                      <div className="truncate text-[13px]" style={{ color: "var(--color-ink)" }}>
                        {m.question}
                      </div>
                      <div className="truncate text-[12px]" style={{ color: "var(--color-ink-muted)" }}>
                        {m.found ? m.answer : "Not found in this filing"}
                        {reference ? ` · ${reference}` : ""}
                      </div>
                    </button>
                );
              })}
            </>
          )}

          {nothing && (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <IconSearchOff className="text-[var(--color-ink-faint)]" />
              <p className="text-[13px]" style={{ color: "var(--color-ink-muted)" }}>
                Nothing matched “{query.trim()}”.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
