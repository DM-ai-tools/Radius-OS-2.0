import { useState } from "react";

const FIELDS = [
  "keyword",
  "primary_keyword",
  "url",
  "url_n",
  "proposed_url",
  "current_url",
  "path",
  "title",
  "name",
  "issue",
  "topic",
  "pillar",
  "page",
  "item",
  "broken_url",
  "source_page",
  "check",
] as const;

export function normItem(value: unknown): string {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

export function rowKeys(row: unknown): string[] {
  if (typeof row === "string") {
    const key = normItem(row);
    return key ? [key] : [];
  }
  if (!row || typeof row !== "object") return [];
  const record = row as Record<string, unknown>;
  const keys: string[] = [];
  for (const field of FIELDS) {
    const key = normItem(record[field]);
    if (key && !keys.includes(key)) keys.push(key);
  }
  return keys;
}

function readInitial(payload: Record<string, unknown>): Set<string> {
  const initial = new Set<string>();
  for (const field of ["deselected_items", "deselected_keywords"] as const) {
    const list = payload[field];
    if (!Array.isArray(list)) continue;
    for (const item of list) {
      for (const key of rowKeys(item)) initial.add(key);
    }
  }
  return initial;
}

export function useStageFocus(payload: Record<string, unknown>) {
  const [deselected, setDeselected] = useState<Set<string>>(() => readInitial(payload));

  function focused(row: unknown): boolean {
    const keys = rowKeys(row);
    if (!keys.length) return true;
    return keys.every((key) => !deselected.has(key));
  }

  function toggle(row: unknown) {
    const keys = rowKeys(row);
    if (!keys.length) return;
    setDeselected((current) => {
      const next = new Set(current);
      const isOn = keys.every((key) => !next.has(key));
      for (const key of keys) {
        if (isOn) next.add(key);
        else next.delete(key);
      }
      return next;
    });
  }

  return {
    focused,
    toggle,
    edits: { deselected_items: [...deselected] } as Record<string, unknown>,
  };
}
