import { useRef, useState, type DragEvent } from "react";
import type { Theme } from "../../lib/hooks/useTheme";
import type { Filing } from "../../lib/types";
import { ACTIVE_STATUSES } from "../../lib/types";
import { Badge, EmptyHint, IconButton, ResizeHandle, Spinner } from "../ui";
import {
  IconChevronLeft,
  IconFile,
  IconMenu,
  IconMonitor,
  IconMoon,
  IconMore,
  IconPlus,
  IconSearch,
  IconSettings,
  IconSun,
} from "../ui/icons";

interface Props {
  filings: Filing[];
  selectedId: string | null;
  collapsed: boolean;
  uploading: boolean;
  view: "active" | "archive";
  theme: Theme;
  width: number;
  resizing: boolean;
  onResizeStart: (e: React.PointerEvent) => void;
  onToggleCollapse: () => void;
  onSelect: (id: string) => void;
  onUpload: (file: File) => void;
  onHome: () => void;
  onOpenSettings: () => void;
  onCycleTheme: () => void;
  onOpenSearch: () => void;
  onSetView: (view: "active" | "archive") => void;
  onFilingMenu: (filing: Filing, anchor: DOMRect) => void;
}

const THEME_ICON: Record<Theme, typeof IconSun> = {
  light: IconSun,
  dark: IconMoon,
  system: IconMonitor,
};

function StatusLine({ filing }: { filing: Filing }) {
  if (filing.status === "failed") {
    return <Badge tone="error">Failed</Badge>;
  }
  if (ACTIVE_STATUSES.includes(filing.status)) {
    return (
      <span className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--color-ink-muted)" }}>
        <Spinner />
        {filing.status_label}
      </span>
    );
  }
  return (
    <span className="text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
      {filing.num_pages ? `${filing.num_pages} pages` : "Ready"}
    </span>
  );
}

export function Sidebar({
  filings,
  selectedId,
  collapsed,
  uploading,
  view,
  theme,
  width,
  resizing,
  onResizeStart,
  onToggleCollapse,
  onSelect,
  onUpload,
  onHome,
  onOpenSettings,
  onCycleTheme,
  onOpenSearch,
  onSetView,
  onFilingMenu,
}: Props) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) onUpload(file);
  }

  // Only a file drag should arm the drop target. Dragging anything else -
  // notably a text selection begun while grabbing the resize handle - used to
  // raise the overlay, and because dragleave is ignored unless it fires on
  // the sidebar itself, it then stayed up over the filing list.
  const isFileDrag = (e: DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes("Files");

  const ThemeIcon = THEME_ICON[theme];

  // The brand mark doubles as the way home, so it is present in both states -
  // collapsing the sidebar should not cost you the one control that always
  // gets you back to a clean start.
  const logo = (
    <button
      onClick={onHome}
      title="Plutus — new filing"
      aria-label="Plutus home"
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[13px] font-semibold
        text-white transition-transform hover:scale-105"
      style={{ background: "var(--color-accent)" }}
    >
      P
    </button>
  );

  return (
    <aside
      onDragOver={(e) => {
        if (!isFileDrag(e)) return;
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragOver(false)}
      onDragEnd={() => setDragOver(false)}
      onMouseLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={`relative flex h-full shrink-0 flex-col border-r ${
        resizing ? "" : "transition-[width] duration-200"
      }`}
      style={{
        width: collapsed ? 60 : width,
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      {dragOver && !resizing && (
        <div
          className="fade-in absolute inset-2 z-20 flex items-center justify-center rounded-xl border-2 border-dashed px-3 text-center text-xs font-medium"
          style={{
            borderColor: "var(--color-accent)",
            background: "var(--color-accent-soft)",
            color: "var(--color-accent-ink)",
          }}
        >
          Drop a PDF or HTML filing
        </div>
      )}

      {/* Header */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-1 px-2 pt-3">
          {logo}
          <IconButton label="Expand sidebar" onClick={onToggleCollapse}>
            <IconMenu />
          </IconButton>
        </div>
      ) : (
        <div className="flex items-center gap-2 px-3 pt-3">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            {logo}
            <button
              onClick={onHome}
              className="truncate text-left text-sm font-semibold"
              style={{ color: "var(--color-ink)" }}
            >
              Plutus
            </button>
          </div>
          <IconButton label="Collapse sidebar" onClick={onToggleCollapse}>
            <IconChevronLeft />
          </IconButton>
        </div>
      )}

      {/* Actions */}
      <div className="px-3 pt-3">
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.htm,.html,.xhtml,application/pdf,text/html"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) onUpload(file);
            e.target.value = "";
          }}
        />
        <button
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          title="Add filing"
          className={`flex w-full items-center rounded-lg py-2 text-sm font-medium text-white transition-opacity disabled:opacity-60 ${
            collapsed ? "justify-center px-0" : "justify-center gap-2 px-3"
          }`}
          style={{ background: "var(--color-accent)" }}
        >
          {uploading ? <Spinner /> : <IconPlus />}
          {!collapsed && (uploading ? "Uploading…" : "Add filing")}
        </button>

        <button
          onClick={onOpenSearch}
          title="Search history (Ctrl K)"
          className={`mt-2 flex w-full items-center rounded-lg py-1.5 text-xs transition-colors hover:bg-[var(--color-hover)] ${
            collapsed ? "justify-center px-0" : "gap-2 px-2"
          }`}
          style={{ color: "var(--color-ink-muted)" }}
        >
          <IconSearch />
          {!collapsed && <span className="flex-1 text-left">Search</span>}
          {!collapsed && <span className="tabular text-[10px] opacity-60">⌘K</span>}
        </button>
      </div>

      {/* Filing list */}
      <div className="mt-3 flex-1 overflow-y-auto px-2 pb-2">
        {!collapsed && (
          <div className="flex items-center gap-1 px-2 pb-1.5">
            {(["active", "archive"] as const).map((v) => (
              <button
                key={v}
                onClick={() => onSetView(v)}
                className="rounded-md px-2 py-1 text-[11px] font-medium uppercase tracking-wide transition-colors"
                style={{
                  color: view === v ? "var(--color-ink)" : "var(--color-ink-faint)",
                  background: view === v ? "var(--color-hover)" : "transparent",
                }}
              >
                {v === "active" ? "Filings" : "Archive"}
              </button>
            ))}
          </div>
        )}

        {filings.length === 0 ? (
          !collapsed && (
            <EmptyHint>
              {view === "archive"
                ? "Nothing archived yet."
                : "No filings yet. Add a PDF or HTML filing to start asking questions."}
            </EmptyHint>
          )
        ) : (
          <ul className="space-y-0.5">
            {filings.map((filing) => {
              const selected = filing.id === selectedId;
              const disabled = filing.status !== "ready";

              return (
                <li key={filing.id} className="group relative">
                  <button
                    onClick={() => !disabled && onSelect(filing.id)}
                    disabled={disabled}
                    title={filing.display_title}
                    className={`flex w-full items-start rounded-lg text-left transition-colors disabled:cursor-not-allowed ${
                      collapsed ? "justify-center px-0 py-2.5" : "gap-2.5 py-2 pl-2.5 pr-9"
                    } ${!selected && !disabled ? "hover:bg-[var(--color-hover)]" : ""}`}
                    style={{ background: selected ? "var(--color-raised)" : "transparent" }}
                  >
                    <IconFile className="mt-0.5 shrink-0" />
                    {!collapsed && (
                      <span className="min-w-0 flex-1">
                        <span
                          className="block truncate text-[13px]"
                          style={{
                            color: "var(--color-ink)",
                            fontWeight: selected ? 600 : 450,
                          }}
                        >
                          {filing.display_title}
                        </span>
                        <span className="mt-0.5 block">
                          <StatusLine filing={filing} />
                        </span>
                      </span>
                    )}
                  </button>

                  {!collapsed && (
                    <button
                      aria-label="Filing options"
                      onClick={(e) => {
                        e.stopPropagation();
                        onFilingMenu(filing, e.currentTarget.getBoundingClientRect());
                      }}
                      className="absolute right-1.5 top-1.5 hidden h-6 w-6 items-center justify-center
                        rounded-md hover:bg-[var(--color-hover)] group-hover:flex"
                      style={{ color: "var(--color-ink-muted)" }}
                    >
                      <IconMore />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Footer */}
      <div
        className={`border-t p-2 ${collapsed ? "flex flex-col items-center gap-1" : "flex items-center gap-1"}`}
        style={{ borderColor: "var(--color-border)" }}
      >
        <span className={collapsed ? "hidden" : "flex-1"} />
        <IconButton
          label={`Theme: ${theme}. Click to change.`}
          onClick={onCycleTheme}
        >
          <ThemeIcon />
        </IconButton>
        <IconButton label="Settings" onClick={onOpenSettings}>
          <IconSettings />
        </IconButton>
      </div>

      {!collapsed && (
        <ResizeHandle side="right" active={resizing} onPointerDown={onResizeStart} />
      )}
    </aside>
  );
}
