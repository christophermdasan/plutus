import { useEffect, useRef } from "react";
import type { Filing } from "../../lib/types";
import { IconArchive, IconEdit, IconRestore, IconTrash } from "../ui/icons";

interface Props {
  filing: Filing;
  anchor: DOMRect;
  onClose: () => void;
  onRename: (filing: Filing) => void;
  onArchive: (filing: Filing) => void;
  onUnarchive: (filing: Filing) => void;
  onDelete: (filing: Filing) => void;
}

/** Per-filing actions. Delete is soft, so it's not styled as destructive-final. */
export function FilingMenu({ filing, anchor, onClose, onRename, onArchive, onUnarchive, onDelete }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const items = [
    { label: "Rename", Icon: IconEdit, run: () => onRename(filing) },
    filing.is_archived
      ? { label: "Unarchive", Icon: IconRestore, run: () => onUnarchive(filing) }
      : { label: "Archive", Icon: IconArchive, run: () => onArchive(filing) },
    { label: "Delete", Icon: IconTrash, run: () => onDelete(filing) },
  ];

  return (
    <div
      ref={ref}
      className="rise fixed z-50 w-44 overflow-hidden rounded-xl border py-1 shadow-xl"
      style={{
        top: Math.min(anchor.bottom + 4, window.innerHeight - 150),
        left: Math.min(anchor.left, window.innerWidth - 190),
        background: "var(--color-raised)",
        borderColor: "var(--color-border)",
      }}
      role="menu"
    >
      {items.map(({ label, Icon, run }) => (
        <button
          key={label}
          role="menuitem"
          onClick={() => { run(); onClose(); }}
          className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] transition-colors hover:bg-[var(--color-hover)]"
          style={{ color: "var(--color-ink)" }}
        >
          <Icon size={14} />
          {label}
        </button>
      ))}
    </div>
  );
}
