import { useState, type FormEvent } from "react";

import { describe, post } from "../api";
import { useAuth } from "../auth";

export function Account() {
  const { session, expired } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (next !== repeat) {
      setMessage("The new passwords differ.");
      return;
    }
    try {
      await post("/api/v1/auth/password", { current_password: current, new_password: next });
      expired(); // every session ends with a new password: sign in again
    } catch (e) {
      setMessage(describe(e));
    }
  }

  return (
    <section className="narrow">
      <h1>Your account</h1>
      <p>
        Signed in as <strong>{session?.user}</strong> with the <strong>{session?.role}</strong> role. Roles are
        changed by an administrator.
      </p>
      <form onSubmit={(e) => void submit(e)}>
        <h2>Change password</h2>
        <label>
          Current password
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label>
          New password (12 to 256 characters, not containing your user name)
          <input type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label>
          Repeat the new password
          <input type="password" autoComplete="new-password" required value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </label>
        {message ? (
          <p className="alert error" role="alert">
            {message}
          </p>
        ) : null}
        <button className="primary" type="submit">
          Change password
        </button>
      </form>
    </section>
  );
}
