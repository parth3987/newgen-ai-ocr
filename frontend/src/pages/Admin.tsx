import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { describe, get, patch, post, query } from "../api";
import { useAuth } from "../auth";
import { Badge, ErrorBox, Loading, Pager, show, useAsync, when } from "../components/ui";
import { ROLES, type AuditEntry, type Overview, type Page, type Role, type UserInfo } from "../types";

export function Admin() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "overview";
  return (
    <section>
      <h1>Administration</h1>
      <div className="tabs" role="tablist">
        {["overview", "audit", "users"].map((t) => (
          <button key={t} type="button" role="tab" aria-selected={t === tab} onClick={() => setParams({ tab: t })}>
            {t}
          </button>
        ))}
      </div>
      {tab === "audit" ? <Audit /> : tab === "users" ? <Users /> : <OverviewPanel />}
    </section>
  );
}

function Counts({ title, counts }: { title: string; counts: Record<string, number> }) {
  return (
    <div className="panel">
      <h3>{title}</h3>
      <ul className="counts">
        {Object.entries(counts).map(([k, v]) => (
          <li key={k}>
            <Badge value={k} /> <strong>{v}</strong>
          </li>
        ))}
        {Object.keys(counts).length === 0 ? <li className="muted">none</li> : null}
      </ul>
    </div>
  );
}

function OverviewPanel() {
  const data = useAsync(() => get<Overview>("/api/v1/admin/overview"), [], { ms: 5000, keepPolling: () => true });
  if (data.error) return <ErrorBox error={data.error} title="Could not load the overview" />;
  if (!data.data) return <Loading />;
  const o = data.data;
  return (
    <>
      <p className="muted small">
        Updated {when(o.generated_at)} (every 5 s) · {o.active_sessions} active session(s)
        {o.oldest_queued_s !== null ? ` · oldest queued job waiting ${Math.round(o.oldest_queued_s)} s` : ""}
      </p>
      <div className="grid">
        <Counts title="Jobs" counts={o.jobs} />
        <Counts title="Documents" counts={o.documents} />
        <Counts title="Review items" counts={o.review} />
        <Counts title="Failures, last 24 h" counts={o.failures_24h} />
      </div>
      <h3>Workers</h3>
      {Object.keys(o.workers).length === 0 ? (
        <p className="muted">No job is running.{o.jobs.queued ? " Jobs are waiting: check that a worker is running." : ""}</p>
      ) : (
        <ul>
          {Object.entries(o.workers).map(([w, n]) => (
            <li key={w} className="mono small">
              {w}: {n} job(s)
            </li>
          ))}
        </ul>
      )}
      <h3>Running</h3>
      <table className="list compact">
        <thead>
          <tr>
            <th>Job</th>
            <th>Stage</th>
            <th>Attempt</th>
            <th>Started</th>
            <th>Lease until</th>
            <th>By</th>
          </tr>
        </thead>
        <tbody>
          {o.running.map((j) => (
            <tr key={j.id}>
              <td>
                <Link to={`/documents/${j.document_id}`}>{j.kind}</Link> <span className="mono small">{j.id.slice(0, 8)}</span>
              </td>
              <td>{j.current_stage ?? "—"}</td>
              <td>{j.attempts}</td>
              <td>{when(j.started_at)}</td>
              <td>{when(j.lease_expires_at)}</td>
              <td>{j.submitted_by}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Recent failures</h3>
      <table className="list compact">
        <thead>
          <tr>
            <th>Job</th>
            <th>Error</th>
            <th>Attempts</th>
            <th>Finished</th>
            <th>By</th>
          </tr>
        </thead>
        <tbody>
          {o.recent_failures.map((j) => (
            <tr key={j.id}>
              <td>
                <Link to={`/documents/${j.document_id}`}>{j.kind}</Link> <span className="mono small">{j.id.slice(0, 8)}</span>
              </td>
              <td>
                <Badge value="failed" /> {j.error_message ?? j.error_code}
              </td>
              <td>{j.attempts}</td>
              <td>{when(j.finished_at)}</td>
              <td>{j.submitted_by}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Audit() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [offset, setOffset] = useState(0);
  const page = useAsync(
    () =>
      get<Page<AuditEntry>>(
        `/api/v1/admin/audit${query({
          event: filters.event,
          actor: filters.actor,
          document_id: filters.document_id,
          since: filters.since ? `${filters.since}T00:00:00` : undefined,
          limit: 50,
          offset,
        })}`,
      ),
    [JSON.stringify(filters), offset],
  );
  const set = (k: string) => (e: { target: { value: string } }) => setDraft({ ...draft, [k]: e.target.value });

  function apply(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setFilters(Object.fromEntries(Object.entries(draft).filter(([, v]) => v)));
  }

  return (
    <>
      <form className="filters" onSubmit={apply} role="search">
        <label>
          Event
          <input placeholder="review.approved or auth." value={draft.event ?? ""} onChange={set("event")} />
        </label>
        <label>
          Actor
          <input value={draft.actor ?? ""} onChange={set("actor")} />
        </label>
        <label>
          Document id
          <input value={draft.document_id ?? ""} onChange={set("document_id")} />
        </label>
        <label>
          Since
          <input type="date" value={draft.since ?? ""} onChange={set("since")} />
        </label>
        <button type="submit" className="primary">
          Apply
        </button>
      </form>
      <p className="muted small">Audit events hold identifiers, hashes and outcome codes, never document content.</p>
      <ErrorBox error={page.error} />
      {page.data ? (
        <>
          <table className="list compact">
            <thead>
              <tr>
                <th>Time</th>
                <th>Event</th>
                <th>Actor</th>
                <th>Document</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((e) => (
                <tr key={e.id}>
                  <td>{when(e.ts)}</td>
                  <td>{e.event}</td>
                  <td>{e.actor}</td>
                  <td className="mono small">
                    {e.document_id ? <Link to={`/documents/${e.document_id}`}>{e.document_id.slice(0, 12)}</Link> : "—"}
                  </td>
                  <td className="small">
                    {Object.entries(e.fields)
                      .filter(([k]) => k !== "document_id")
                      .map(([k, v]) => `${k}=${show(v)}`)
                      .join(" ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={page.data} onOffset={setOffset} />
        </>
      ) : (
        <Loading />
      )}
    </>
  );
}

function Users() {
  const { session } = useAuth();
  const users = useAsync(() => get<UserInfo[]>("/api/v1/admin/users"), []);
  const [form, setForm] = useState({ name: "", role: "viewer" as Role, password: "" });
  const [message, setMessage] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, done: string) {
    setMessage(null);
    try {
      await action();
      setMessage(done);
      users.reload();
    } catch (e) {
      setMessage(describe(e));
    }
  }

  return (
    <>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          void run(() => post("/api/v1/admin/users", form), `Created ${form.name}. Share the password privately.`).then(() =>
            setForm({ ...form, name: "", password: "" }),
          );
        }}
      >
        <label>
          New user
          <input required pattern="[a-z0-9][a-z0-9._\-]{0,63}" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label>
          Role
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            {ROLES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label>
          Initial password (12+ characters)
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
        </label>
        <button type="submit" className="primary">
          Create
        </button>
      </form>
      {message ? <p className="alert info">{message}</p> : null}
      <ErrorBox error={users.error} />
      <table className="list">
        <thead>
          <tr>
            <th>User</th>
            <th>Role</th>
            <th>Status</th>
            <th>Last sign-in</th>
            <th>Password changed</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(users.data ?? []).map((u) => (
            <tr key={u.name}>
              <td>{u.name}</td>
              <td>
                <select
                  aria-label={`Role of ${u.name}`}
                  value={u.role}
                  disabled={u.name === session?.user}
                  onChange={(e) =>
                    void run(() => patch(`/api/v1/admin/users/${u.name}`, { role: e.target.value }), `${u.name} is now ${e.target.value}.`)
                  }
                >
                  {ROLES.map((r) => (
                    <option key={r}>{r}</option>
                  ))}
                </select>
              </td>
              <td>
                {u.disabled ? <Badge value="disabled" /> : <Badge value="active" />}
                {u.locked_until && new Date(u.locked_until) > new Date() ? <Badge value="locked" title={`until ${when(u.locked_until)}`} /> : null}
              </td>
              <td>{when(u.last_login_at)}</td>
              <td>{when(u.password_changed_at)}</td>
              <td>
                {u.name !== session?.user ? (
                  <button
                    type="button"
                    onClick={() =>
                      void run(
                        () => patch(`/api/v1/admin/users/${u.name}`, { disabled: !u.disabled }),
                        `${u.name} ${u.disabled ? "enabled" : "disabled"}.`,
                      )
                    }
                  >
                    {u.disabled ? "Enable" : "Disable"}
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
