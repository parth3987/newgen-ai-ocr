import { useState, type FormEvent } from "react";

import { describe } from "../api";
import { useAuth } from "../auth";

export function Login() {
  const { login, notice } = useAuth();
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(user.trim(), password);
    } catch (e) {
      setError(describe(e));
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form onSubmit={(e) => void submit(e)} aria-labelledby="login-title">
        <h1 id="login-title">New Gen AI OCR</h1>
        <p className="muted">Sign in with the account your administrator created.</p>
        {notice ? <p className="alert info">{notice}</p> : null}
        <label>
          User name
          <input autoComplete="username" value={user} onChange={(e) => setUser(e.target.value)} required />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error ? (
          <p className="alert error" role="alert">
            {error}
          </p>
        ) : null}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
