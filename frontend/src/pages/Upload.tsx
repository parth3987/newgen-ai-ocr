import { useRef, useState, type DragEvent } from "react";
import { Link } from "react-router";

import { describe, get, upload } from "../api";
import { Badge, JobProgress, useAsync } from "../components/ui";
import type { Job, JobSubmitted, SchemaSummary } from "../types";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff";
const LANGUAGES = ["auto", "en", "hi", "mr", "te", "ta", "kn"];
const PARALLEL = 3;
const DONE = new Set(["succeeded", "failed", "cancelled"]);

interface Item {
  key: string;
  file: File;
  state: "waiting" | "uploading" | "submitted" | "error";
  progress: number;
  job: Job | null;
  duplicate: boolean;
  error: string | null;
}

function idempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return `ui-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

export function Upload() {
  const [items, setItems] = useState<Item[]>([]);
  const [documentType, setDocumentType] = useState("auto");
  const [languages, setLanguages] = useState("auto");
  const [force, setForce] = useState(false);
  const [running, setRunning] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const schemas = useAsync(() => get<SchemaSummary[]>("/api/v1/extraction/schemas"), []);

  const update = (key: string, change: Partial<Item>) =>
    setItems((list) => list.map((it) => (it.key === key ? { ...it, ...change } : it)));

  function add(files: FileList | null) {
    if (!files) return;
    const added = Array.from(files).map((file) => ({
      key: idempotencyKey(), // also the request's Idempotency-Key: a retry never duplicates
      file,
      state: "waiting" as const,
      progress: 0,
      job: null,
      duplicate: false,
      error: null,
    }));
    setItems((list) => [...list, ...added]);
  }

  async function send(item: Item) {
    update(item.key, { state: "uploading", error: null, progress: 0 });
    const form = new FormData();
    form.append("file", item.file);
    form.append("document_type", documentType);
    form.append("languages", languages);
    if (force) form.append("force", "true");
    try {
      const result = await upload<JobSubmitted>("/api/v1/documents", form, { "Idempotency-Key": item.key }, (p) =>
        update(item.key, { progress: p.total ? p.loaded / p.total : 0 }),
      );
      update(item.key, { state: "submitted", job: result.job, duplicate: result.duplicate, progress: 1 });
      void follow(item.key, result.job.id);
    } catch (e) {
      update(item.key, { state: "error", error: describe(e) });
    }
  }

  async function follow(key: string, jobId: string) {
    for (;;) {
      try {
        const job = await get<Job>(`/api/v1/jobs/${jobId}`);
        update(key, { job });
        if (DONE.has(job.status)) return;
      } catch {
        return; // shown on the documents page
      }
      await new Promise((r) => setTimeout(r, 2500));
    }
  }

  async function start() {
    setRunning(true);
    const queue = items.filter((it) => it.state === "waiting" || it.state === "error");
    const workers = Array.from({ length: PARALLEL }, async () => {
      for (let next = queue.shift(); next; next = queue.shift()) await send(next);
    });
    await Promise.all(workers);
    setRunning(false);
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    add(event.dataTransfer.files);
  }

  const pending = items.filter((it) => it.state === "waiting" || it.state === "error").length;

  return (
    <section>
      <h1>Upload documents</h1>
      <p className="muted">
        PDF, JPEG, PNG or TIFF. Each file is checked, then processed in the background: preprocessing, OCR, layout,
        extraction and the review queue.
      </p>
      <div
        className="dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={drop}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        role="button"
        tabIndex={0}
      >
        Drop files here, or click to choose (several at once for a batch)
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(e) => add(e.target.files)} />
      </div>
      <div className="filters">
        <label>
          Document type
          <select value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
            <option value="auto">detect automatically</option>
            {(schemas.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Language
          <select value={languages} onChange={(e) => setLanguages(e.target.value)}>
            {LANGUAGES.map((l) => (
              <option key={l} value={l}>
                {l === "auto" ? "detect per page" : l}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
          Process again even if I uploaded the same file before
        </label>
        <button type="button" className="primary" disabled={running || pending === 0} onClick={() => void start()}>
          {running ? "Uploading…" : `Upload ${pending} file(s)`}
        </button>
        <button type="button" disabled={running} onClick={() => setItems([])}>
          Clear list
        </button>
      </div>
      {items.length ? (
        <table className="list">
          <thead>
            <tr>
              <th>File</th>
              <th>Upload</th>
              <th>Processing</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.key}>
                <td>{it.file.name}</td>
                <td>
                  {it.state === "uploading" ? (
                    <progress max={1} value={it.progress} aria-label="upload progress" />
                  ) : it.state === "error" ? (
                    <span className="bad-text" role="alert">
                      {it.error}
                    </span>
                  ) : (
                    <Badge value={it.state} />
                  )}
                  {it.duplicate ? <div className="small muted">already uploaded: showing the earlier job</div> : null}
                </td>
                <td>
                  {it.job ? (
                    <>
                      <JobProgress job={it.job} />{" "}
                      <Link to={`/documents/${it.job.document_id}`}>open</Link>
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
