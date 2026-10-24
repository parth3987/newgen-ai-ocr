import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { describe } from "../api";
import type { Job, Page } from "../types";

// ------------------------------------------------------------ data loading

export interface Async<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/** Load data; ``poll`` (ms) reloads while ``keepPolling(data)`` is true. */
export function useAsync<T>(
  load: () => Promise<T>,
  deps: unknown[],
  poll?: { ms: number; keepPolling: (data: T) => boolean },
): Async<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const loader = useRef(load);
  loader.current = load;

  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    setLoading(true);
    loader
      .current()
      .then((value) => {
        if (!live) return;
        setData(value);
        setError(null);
        if (poll && poll.keepPolling(value)) timer = window.setTimeout(() => setTick((t) => t + 1), poll.ms);
      })
      .catch((e: unknown) => live && setError(e))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

// ------------------------------------------------------------ display

export function ErrorBox({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  return (
    <div className="alert error" role="alert">
      {title ? <strong>{title}: </strong> : null}
      {describe(error)}
    </div>
  );
}

export function Notice({ children, kind = "info" }: { children: ReactNode; kind?: "info" | "warn" | "ok" }) {
  return (
    <div className={`alert ${kind}`} role="status">
      {children}
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <p className="muted" aria-live="polite">{what}…</p>;
}

const TONE: Record<string, string> = {
  succeeded: "ok",
  ready: "ok",
  complete: "ok",
  approved: "ok",
  auto_accepted: "ok",
  verified: "ok",
  passed: "ok",
  extracted: "ok",
  running: "busy",
  queued: "busy",
  received: "busy",
  in_review: "busy",
  pending: "warn",
  needs_review: "warn",
  ambiguous: "warn",
  conflict: "warn",
  missing: "warn",
  unclassified: "warn",
  cancelled: "muted",
  not_applicable: "muted",
  failed: "bad",
  rejected: "bad",
  invalid: "bad",
};

/** A status as text and colour (the colour is never the only signal). */
export function Badge({ value, title }: { value: string | null | undefined; title?: string }) {
  if (!value) return null;
  return (
    <span className={`badge ${TONE[value] ?? ""}`} title={title}>
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function JobProgress({ job }: { job: Job | null }) {
  if (!job) return <span className="muted">no job</span>;
  const done = job.completed_stages.length;
  const total = job.stages.length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const label =
    job.status === "running"
      ? `${job.current_stage ?? "starting"} (${done}/${total})`
      : job.status === "queued" && job.attempts > 0
        ? `retry ${job.attempts + 1} of ${job.max_attempts} waiting`
        : `${done}/${total} stages`;
  return (
    <span className="progress-cell">
      <Badge value={job.status} />
      <progress max={total} value={done} aria-label={`${pct}% of stages done`} />
      <span className="muted small">{label}</span>
      {job.error_code ? <span className="badge bad">{job.error_code}</span> : null}
    </span>
  );
}

export function Pager<T>({ page, onOffset }: { page: Page<T>; onOffset: (offset: number) => void }) {
  const first = page.total === 0 ? 0 : page.offset + 1;
  const last = Math.min(page.offset + page.limit, page.total);
  return (
    <div className="pager">
      <span className="muted">
        {first}–{last} of {page.total}
      </span>
      <button type="button" disabled={page.offset === 0} onClick={() => onOffset(Math.max(0, page.offset - page.limit))}>
        Previous
      </button>
      <button type="button" disabled={last >= page.total} onClick={() => onOffset(page.offset + page.limit)}>
        Next
      </button>
    </div>
  );
}

export function when(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / 1024 / 1024).toFixed(1)} MiB`;
}

export function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
