import { useEffect, useState } from "react";
import { filings as filingsApi } from "../../lib/api";
import type { SourceRef } from "../../lib/types";
import { Badge, IconButton, ResizeHandle } from "../ui";
import { IconCheck, IconChevronLeft, IconChevronRight, IconX } from "../ui/icons";

interface Props {
  source: SourceRef | null;
  maxPage: number | null;
  /** Chooses the viewer. Filings arrive as PDF or as EDGAR HTML. */
  mediaKind?: "pdf" | "html";
  width: number;
  resizing: boolean;
  onResizeStart: (e: React.PointerEvent) => void;
  onClose: () => void;
}

/**
 * Slides over the chat instead of occupying a permanent column.
 *
 * The old layout reserved a third of the screen for a panel that said
 * "answer a question and it will appear here" - space spent on a promise
 * rather than on content. Here the chat stays full width until there is an
 * actual citation to show, and the drawer opens straight to the cited page.
 */
export function SourceDrawer({
  source,
  maxPage,
  mediaKind = "pdf",
  width,
  resizing,
  onResizeStart,
  onClose,
}: Props) {
  const [page, setPage] = useState<number | null>(source?.page ?? null);

  // Reopening for a different citation must jump to that page, including
  // when the drawer was already open on another one.
  useEffect(() => {
    setPage(source?.page ?? null);
  }, [source?.page, source?.filingId]);

  useEffect(() => {
    if (!source) return;
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [source, onClose]);

  if (!source || page === null) return null;

  const atStart = page <= 1;
  const atEnd = maxPage !== null && page >= maxPage;

  // Citation chips show the number printed on the page, so the navigator
  // has to as well - one view showing "p. 159" beside "161 / 306" reads as
  // a bug. The offset is a property of the filing's front matter, constant
  // across it, so it carries while paging.
  const offset =
    source.label != null ? source.label - source.page : 0;
  const printed = (n: number) => (n + offset >= 1 ? n + offset : n);

  return (
    <aside
      className={`slide-in-right relative flex h-full shrink-0 flex-col border-l ${
        resizing ? "" : "transition-[width] duration-150"
      }`}
      style={{
        width,
        background: "var(--color-raised)",
        borderColor: "var(--color-border)",
      }}
      aria-label="Source document"
    >
      <ResizeHandle side="left" active={resizing} onPointerDown={onResizeStart} />
      <header
        className="flex h-14 shrink-0 items-center gap-2 border-b px-4"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {source.quote ? (
              <Badge tone="verified">
                <IconCheck size={10} /> Source
              </Badge>
            ) : (
              <Badge tone="neutral">Document</Badge>
            )}
            <span className="truncate text-[13px] font-medium" style={{ color: "var(--color-ink)" }}>
              {source.filingName}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-0.5">
          <IconButton label="Previous page" disabled={atStart} onClick={() => setPage((p) => (p ?? 1) - 1)}>
            <IconChevronLeft />
          </IconButton>
          <span className="tabular px-1 text-[12px] whitespace-nowrap" style={{ color: "var(--color-ink-muted)" }}>
            {printed(page)}
            {maxPage ? ` / ${printed(maxPage)}` : ""}
          </span>
          <IconButton label="Next page" disabled={atEnd} onClick={() => setPage((p) => (p ?? 1) + 1)}>
            <IconChevronRight />
          </IconButton>
          <IconButton label="Close source" onClick={onClose}>
            <IconX />
          </IconButton>
        </div>
      </header>

      {/* The quote, so the exact evidence is readable without hunting the page.
          Absent when the reader opened the document directly rather than
          following a citation - there is no claim to evidence in that case. */}
      {source.quote && (
      <div className="shrink-0 border-b px-4 py-3" style={{ borderColor: "var(--color-border)" }}>
        {source.question && (
          <p className="mb-1.5 text-[12px]" style={{ color: "var(--color-ink-faint)" }}>
            {source.question}
          </p>
        )}
        <blockquote
          className="rounded-r-md border-l-[3px] py-1 pl-3 text-[13px] italic leading-relaxed"
          style={{ borderColor: "var(--color-verified)", color: "var(--color-ink)" }}
        >
          “{source.quote}”
        </blockquote>
        <p className="tabular mt-2 text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
          Quoted from page {printed(source.page)}
        </p>
      </div>
      )}

      <div className="min-h-0 flex-1" style={{ background: "var(--color-surface)" }}>
        <iframe
          // Remounting on page change is what makes the #page anchor take
          // effect - browsers ignore a fragment change on a loaded PDF.
          key={`${source.filingId}-${page}`}
          title={`${source.filingName}, page ${page}`}
          src={
            mediaKind === "html"
              ? filingsApi.pageUrl(source.filingId, page)
              : filingsApi.pdfUrl(source.filingId, page)
          }
          // A filing is uploaded content, so its markup is rendered with
          // every capability withdrawn: no scripts, no forms, no same-origin
          // access to the app. The server strips executable content too -
          // this is the second of the two layers, not the only one.
          // PDFs are left to the browser's own viewer, which is already
          // sandboxed and needs these privileges to run.
          sandbox={mediaKind === "html" ? "" : undefined}
          className="h-full w-full border-0"
        />
      </div>
    </aside>
  );
}
