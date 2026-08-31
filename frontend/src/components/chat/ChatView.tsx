import { useEffect, useRef, useState } from "react";
import type { AnswerResult, Filing, SourceRef } from "../../lib/types";
import { Badge, IconButton, Spinner, ThinkingDots, formatLatency } from "../ui";
import {
  IconAlert,
  IconCheck,
  IconCopy,
  IconSearchOff,
  IconSend,
  IconPanelRight,
  IconPlus,
  IconSparkle,
  IconThumbDown,
  IconThumbUp,
} from "../ui/icons";

export interface Turn {
  id: string;
  question: string;
  result?: AnswerResult;
  error?: { message: string; hint?: string; rateLimited?: boolean };
  pending?: boolean;
  feedback?: number | null;
}

interface Props {
  filing: Filing | null;
  turns: Turn[];
  activeSource: SourceRef | null;
  uploading: boolean;
  sourceOpen: boolean;
  onUpload: (file: File) => void;
  onToggleDocument: () => void;
  onAsk: (question: string) => void;
  onOpenSource: (ref: SourceRef) => void;
  onFeedback: (turn: Turn, value: number | null) => void;
  onCopy: (turn: Turn) => void;
}

const FALLBACK_SUGGESTIONS = [
  "What was total revenue?",
  "Were any impairment charges recorded?",
  "What is the maturity date of long-term debt?",
];

/** The citation chip: the product's core promise, inline where the claim is. */
function Citation({
  page,
  label,
  active,
  onClick,
}: {
  page: number;
  label?: number | null;
  active: boolean;
  onClick: () => void;
}) {
  // The number printed on the page, which is what a reader can match in
  // their own copy. It runs behind the sequential index on a filing whose
  // front matter is counted but not numbered.
  const shown = label ?? page;
  return (
    <button
      onClick={onClick}
      title={
        shown === page
          ? `Open page ${page} in the filing`
          : `Open printed page ${shown} (sheet ${page}) in the filing`
      }
      className="tabular ml-1.5 inline-flex translate-y-[-1px] items-center gap-1 rounded-md px-1.5 py-0.5
        text-[11px] font-medium leading-none transition-colors"
      style={{
        background: active ? "var(--color-accent)" : "var(--color-accent-soft)",
        color: active ? "#fff" : "var(--color-accent-ink)",
      }}
    >
      <IconCheck size={10} />p.&nbsp;{shown}
    </button>
  );
}

function AnswerCard({
  turn,
  activeSource,
  onOpenSource,
  onFeedback,
  onCopy,
  filing,
}: {
  turn: Turn;
  activeSource: SourceRef | null;
  onOpenSource: (ref: SourceRef) => void;
  onFeedback: (turn: Turn, value: number | null) => void;
  onCopy: (turn: Turn) => void;
  filing: Filing;
}) {
  const result = turn.result!;

  // The same figure is usually printed in more than one place, and each is
  // a truthful citation. All of them get a chip so the reader can check the
  // number against the statement, the MD&A or a note as they prefer. Older
  // answers stored before citations were persisted carry only page/quote,
  // so that is the fallback rather than showing nothing.
  const citations =
    result.citations?.length > 0
      ? result.citations
      : result.page != null
        ? [{ page: result.page, quote: result.quote }]
        : [];

  return (
    <div className="max-w-[46rem]">
      <p className="text-[15px] leading-[1.7]" style={{ color: "var(--color-ink)" }}>
        {result.answer}
        {citations.map((citation, index) => (
          <Citation
            key={`${citation.page}-${index}`}
            page={citation.page}
            label={citation.label}
            active={
              activeSource?.page === citation.page && activeSource?.filingId === filing.id
            }
            onClick={() =>
              onOpenSource({
                filingId: filing.id,
                filingName: filing.display_title,
                page: citation.page,
                label: citation.label,
                quote: citation.quote,
                question: turn.question,
                answer: result.answer,
              })
            }
          />
        ))}
      </p>

      <blockquote
        className="mt-3 rounded-r-md border-l-[3px] py-1.5 pl-3 pr-2 text-[13px] italic leading-relaxed"
        style={{ borderColor: "var(--color-verified)", color: "var(--color-ink-muted)" }}
      >
        “{citations[0]?.quote ?? result.quote}”
      </blockquote>

      {citations.length > 1 && (
        <p className="mt-1.5 text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
          Also reported on {citations.length - 1} other{" "}
          {citations.length === 2 ? "page" : "pages"} — click any chip to check it there.
        </p>
      )}

      <div className="mt-2.5 flex items-center gap-1">
        <Badge tone="verified">
          <IconCheck size={10} /> Verified
        </Badge>
        <span className="tabular text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
          {formatLatency(result.latency_ms)}
        </span>
        <span className="flex-1" />
        <IconButton label="Copy answer" onClick={() => onCopy(turn)}>
          <IconCopy size={13} />
        </IconButton>
        <IconButton
          label="Helpful"
          onClick={() => onFeedback(turn, turn.feedback === 1 ? null : 1)}
          style={turn.feedback === 1 ? { color: "var(--color-verified)" } : undefined}
        >
          <IconThumbUp size={13} />
        </IconButton>
        <IconButton
          label="Not helpful"
          onClick={() => onFeedback(turn, turn.feedback === -1 ? null : -1)}
          style={turn.feedback === -1 ? { color: "var(--color-error)" } : undefined}
        >
          <IconThumbDown size={13} />
        </IconButton>
      </div>
    </div>
  );
}

/** Declining is a correct outcome, so it reads as information, not failure. */
function NotFoundCard({ result }: { result: AnswerResult }) {
  const [showWork, setShowWork] = useState(false);

  return (
    <div className="max-w-[46rem]">
      <div className="flex items-center gap-2 text-[15px] font-medium" style={{ color: "var(--color-ink)" }}>
        <IconSearchOff className="shrink-0 text-[var(--color-ink-muted)]" />
        Not found in this filing
      </div>
      <p className="mt-1.5 text-[13px] leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
        {result.reason}
      </p>

      {result.considered.length > 0 && (
        <>
          <button
            onClick={() => setShowWork((v) => !v)}
            className="mt-2 text-[12px] underline-offset-2 hover:underline"
            style={{ color: "var(--color-ink-faint)" }}
          >
            {showWork ? "Hide" : "Show"} what was checked
          </button>

          {showWork && (
            <div className="mt-2 space-y-1.5">
              {result.considered.map((c, i) => (
                <div
                  key={i}
                  className="rounded-lg border px-3 py-2 text-[12px]"
                  style={{ borderColor: "var(--color-border)", color: "var(--color-ink-muted)" }}
                >
                  <div className="tabular mb-1 text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
                    Page {c.page} · relevance {c.score.toFixed(1)}
                  </div>
                  {c.excerpt}…
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ErrorCard({ error }: { error: NonNullable<Turn["error"]> }) {
  const tone = error.rateLimited ? "warn" : "error";
  const color = error.rateLimited ? "var(--color-warn)" : "var(--color-error)";
  const background = error.rateLimited ? "var(--color-warn-soft)" : "var(--color-error-soft)";

  return (
    <div
      className="max-w-[46rem] rounded-xl px-4 py-3"
      style={{ background }}
    >
      <div className="flex items-center gap-2 text-[14px] font-medium" style={{ color }}>
        <IconAlert className="shrink-0" />
        {error.rateLimited ? "AI usage limit reached" : "Couldn't answer that"}
      </div>
      <p className="mt-1 text-[13px] leading-relaxed" style={{ color }}>
        {error.message}
      </p>
      {error.hint && (
        <p className="mt-1 text-[12px] opacity-80" style={{ color }}>
          {error.hint}
        </p>
      )}
      <Badge tone={tone}>{error.rateLimited ? "Temporary" : "Error"}</Badge>
    </div>
  );
}

export function ChatView({
  filing,
  turns,
  activeSource,
  uploading,
  sourceOpen,
  onUpload,
  onToggleDocument,
  onAsk,
  onOpenSource,
  onFeedback,
  onCopy,
}: Props) {
  const [draft, setDraft] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [filing?.id]);

  const asking = turns.some((turn) => turn.pending);
  const canAsk = filing?.status === "ready" && !asking;

  function submit(question?: string) {
    const q = (question ?? draft).trim();
    if (!q || !canAsk) return;
    onAsk(q);
    setDraft("");
  }

  const suggestions =
    filing?.suggested_questions?.length ? filing.suggested_questions : FALLBACK_SUGGESTIONS;

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col" style={{ background: "var(--color-canvas)" }}>
      {/* Header */}
      <header
        className="flex h-14 shrink-0 items-center gap-3 border-b px-6"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
            {filing ? filing.display_title : "Plutus"}
          </h1>
          {filing && (
            <p className="truncate text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
              {filing.original_name}
              {filing.num_pages ? ` · ${filing.num_pages} pages` : ""}
            </p>
          )}
        </div>

        {/* Reading the filing belongs beside the filing's own title, not in
            the list of filings - and it is a first-class act, not something
            you have to ask a question to earn. */}
        {filing && filing.status === "ready" && (
          <button
            onClick={onToggleDocument}
            title={sourceOpen ? "Hide document" : "View document"}
            aria-pressed={sourceOpen}
            className="flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px]
              font-medium transition-colors hover:bg-[var(--color-hover)]"
            style={{
              borderColor: sourceOpen ? "var(--color-accent)" : "var(--color-border-strong)",
              color: sourceOpen ? "var(--color-accent-ink)" : "var(--color-ink-muted)",
              background: sourceOpen ? "var(--color-accent-soft)" : "transparent",
            }}
          >
            <IconPanelRight size={14} />
            {sourceOpen ? "Hide document" : "View document"}
          </button>
        )}
      </header>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-8">
          {!filing && (
            /* The start screen. Reached on first load and whenever the brand
               mark is clicked, so there is always a way back to "begin a new
               filing" without hunting through the sidebar. */
            <div className="pt-16 text-center">
              <h2 className="text-[24px] font-semibold tracking-tight" style={{ color: "var(--color-ink)" }}>
                Start a new filing
              </h2>
              <p className="mx-auto mt-2 max-w-md text-[14px] leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                Add a 10-K, 10-Q or 8-K as a PDF or the HTML EDGAR serves, then ask
                anything. Every answer comes with the page it came from — or an
                honest “not found”.
              </p>

              <input
                ref={fileRef}
                type="file"
                accept=".pdf,.htm,.html,.xhtml,application/pdf,text/html"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onUpload(f);
                  e.target.value = "";
                }}
              />

              <button
                onClick={() => fileRef.current?.click()}
                onDragOver={(e) => {
                  if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDragEnd={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  const f = e.dataTransfer.files?.[0];
                  if (f) onUpload(f);
                }}
                disabled={uploading}
                className="mx-auto mt-8 flex w-full max-w-md flex-col items-center gap-2 rounded-2xl
                  border-2 border-dashed px-6 py-10 transition-colors disabled:opacity-60"
                style={{
                  borderColor: dragOver ? "var(--color-accent)" : "var(--color-border-strong)",
                  background: dragOver ? "var(--color-accent-soft)" : "transparent",
                }}
              >
                <span
                  className="flex h-10 w-10 items-center justify-center rounded-xl text-white"
                  style={{ background: "var(--color-accent)" }}
                >
                  {uploading ? <Spinner /> : <IconPlus />}
                </span>
                <span className="mt-1 text-[14px] font-medium" style={{ color: "var(--color-ink)" }}>
                  {uploading ? "Uploading…" : "Choose a filing or drop it here"}
                </span>
                <span className="text-[12px]" style={{ color: "var(--color-ink-faint)" }}>
                  PDF, HTM or HTML · up to 100MB
                </span>
              </button>
            </div>
          )}

          {filing && turns.length === 0 && (
            <div className="pt-10">
              <h2 className="text-[19px] font-semibold" style={{ color: "var(--color-ink)" }}>
                Ask anything about this filing
              </h2>
              <p className="mt-1.5 text-[13px]" style={{ color: "var(--color-ink-muted)" }}>
                Answers are checked against the document before you see them.
              </p>
              <div className="mt-5 flex flex-col items-start gap-2">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    onClick={() => submit(s)}
                    disabled={!canAsk}
                    className="flex items-center gap-2 rounded-xl border px-3.5 py-2 text-[13px] transition-colors
                      hover:bg-[var(--color-hover)] disabled:opacity-50"
                    style={{ borderColor: "var(--color-border)", color: "var(--color-ink)" }}
                  >
                    <IconSparkle size={13} className="shrink-0 text-[var(--color-accent)]" />
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-8">
            {turns.map((turn) => (
              <div key={turn.id} className="rise space-y-4">
                <div className="flex justify-end">
                  <div
                    className="max-w-[80%] rounded-2xl rounded-br-md px-4 py-2.5 text-[15px] leading-relaxed"
                    style={{ background: "var(--color-surface)", color: "var(--color-ink)" }}
                  >
                    {turn.question}
                  </div>
                </div>

                {turn.pending && (
                  <div className="flex items-center gap-2.5">
                    <ThinkingDots />
                    <span className="text-[13px]" style={{ color: "var(--color-ink-faint)" }}>
                      Reading the filing…
                    </span>
                  </div>
                )}

                {turn.error && <ErrorCard error={turn.error} />}

                {turn.result?.found && filing && (
                  <AnswerCard
                    turn={turn}
                    filing={filing}
                    activeSource={activeSource}
                    onOpenSource={onOpenSource}
                    onFeedback={onFeedback}
                    onCopy={onCopy}
                  />
                )}

                {turn.result && !turn.result.found && <NotFoundCard result={turn.result} />}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Composer */}
      <div className="shrink-0 px-6 pb-6">
        <div className="mx-auto max-w-3xl">
          <div
            // No focus ring on the composer: it is the page's primary control
            // and is almost always focused, so highlighting it marks the
            // resting state rather than anything meaningful.
            className="flex items-end gap-2 rounded-2xl border p-2 pl-4 shadow-sm"
            style={{ background: "var(--color-raised)", borderColor: "var(--color-border-strong)" }}
          >
            <textarea
              ref={inputRef}
              data-composer
              value={draft}
              rows={1}
              onChange={(e) => {
                setDraft(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
              }}
              onKeyDown={(e) => {
                // Enter sends; Shift+Enter adds a line, matching every chat
                // app people already use.
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              disabled={!canAsk}
              placeholder={
                !filing
                  ? "Select a filing first"
                  : asking
                    ? "Reading the filing…"
                  : !canAsk
                    ? `${filing.status_label}…`
                    : "Ask about this filing…"
              }
              className="max-h-40 flex-1 resize-none bg-transparent py-2 text-[15px] leading-relaxed
                placeholder:text-[var(--color-ink-faint)] focus:outline-none disabled:cursor-not-allowed"
              style={{ color: "var(--color-ink)" }}
            />
            <button
              onClick={() => submit()}
              disabled={!draft.trim() || !canAsk}
              aria-label="Send"
              className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white
                transition-opacity disabled:opacity-30"
              style={{ background: "var(--color-accent)" }}
            >
              <IconSend />
            </button>
          </div>
          <p className="mt-2 text-center text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
            Answers are verified against the filing. If the evidence isn’t there, it says so.
          </p>
        </div>
      </div>
    </div>
  );
}
