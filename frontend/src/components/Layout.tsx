import { NavLink, Outlet } from "react-router";

import { atLeast, useAuth } from "../auth";

export function Layout() {
  const { session, logout } = useAuth();
  const role = session?.role;
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">New Gen AI OCR</span>
        <nav aria-label="Main">
          <NavLink to="/documents">Documents</NavLink>
          <NavLink to="/upload">Upload</NavLink>
          <NavLink to="/extractions">Results</NavLink>
          {atLeast(role, "viewer") ? <NavLink to="/review">Review</NavLink> : null}
          {atLeast(role, "admin") ? <NavLink to="/admin">Admin</NavLink> : null}
        </nav>
        <span className="who">
          <NavLink to="/account">
            {session?.user} · {role}
          </NavLink>
          <button type="button" onClick={() => void logout()}>
            Sign out
          </button>
        </span>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}
