import { useCallback, useEffect, useRef, useState } from "react";

interface Options {
  /** Persisted under this key so a chosen width survives a reload. */
  storageKey: string;
  defaultWidth: number;
  min: number;
  max: number;
  /** Which edge the handle sits on: dragging left widens a right-hand panel. */
  edge?: "left" | "right";
}

function stored(key: string, fallback: number): number {
  try {
    const v = Number(localStorage.getItem(key));
    return Number.isFinite(v) && v > 0 ? v : fallback;
  } catch {
    return fallback;
  }
}

/**
 * Drag-to-resize for a panel, in pixels.
 *
 * Listeners are bound to the window rather than the handle: the pointer
 * routinely outruns a 4px target mid-drag, and a handle-bound listener drops
 * the gesture the moment it does. Width is tracked in a ref during the drag
 * so each move reads the committed value rather than a stale closure.
 */
export function useResizable({ storageKey, defaultWidth, min, max, edge = "right" }: Options) {
  const [width, setWidth] = useState(() => stored(storageKey, defaultWidth));
  const [dragging, setDragging] = useState(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      startX.current = e.clientX;
      startWidth.current = width;
      setDragging(true);
    },
    [width],
  );

  useEffect(() => {
    if (!dragging) return;

    function onMove(e: PointerEvent) {
      const delta = e.clientX - startX.current;
      const next = startWidth.current + (edge === "right" ? delta : -delta);
      setWidth(Math.min(max, Math.max(min, next)));
    }
    function onUp() {
      setDragging(false);
    }

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    // While dragging, the cursor should not flicker into a text caret as it
    // passes over labels, and a drag must not select the text underneath.
    const previousCursor = document.body.style.cursor;
    const previousSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousSelect;
    };
  }, [dragging, edge, min, max]);

  useEffect(() => {
    if (dragging) return; // persist the settled width, not every frame
    try {
      localStorage.setItem(storageKey, String(width));
    } catch {
      /* preference won't persist */
    }
  }, [width, dragging, storageKey]);

  return { width, dragging, onPointerDown, reset: () => setWidth(defaultWidth) };
}
