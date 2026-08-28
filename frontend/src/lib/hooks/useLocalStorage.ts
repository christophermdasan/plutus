import { useState } from "react";

/** State that survives a reload. Falls back to in-memory if storage is blocked. */
export function useLocalStorage<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });

  return [
    value,
    (next: T) => {
      setValue(next);
      try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* ignore */ }
    },
  ];
}
