import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AuthProvider } from "./auth";

type Handler = (path: string, init: RequestInit) => [number, unknown];

function serve(handler: Handler) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit) => {
      calls.push(`${init.method ?? "GET"} ${path}`);
      const [status, body] = handler(path, init);
      return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    }),
  );
  return calls;
}

function mount(path = "/documents") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const unauthenticated: [number, unknown] = [401, { error: { code: "UNAUTHENTICATED", message: "A valid bearer token is required" } }];
const empty = { items: [], total: 0, limit: 25, offset: 0 };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("app", () => {
  it("asks to sign in, and shows why a sign-in failed", async () => {
    serve((path) =>
      path.endsWith("/auth/login")
        ? [401, { error: { code: "LOGIN_FAILED", message: "Wrong user name or password, or account locked" } }]
        : unauthenticated,
    );
    mount();
    fireEvent.change(await screen.findByLabelText("User name"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Wrong user name or password");
  });

  it("signs in and shows only the pages the role may use", async () => {
    let signedIn = false;
    const calls = serve((path) => {
      if (path.endsWith("/auth/login")) {
        signedIn = true;
        return [200, { user: "alice", role: "submitter", csrf_token: "c" }];
      }
      if (!signedIn) return unauthenticated;
      return [200, empty];
    });
    mount();
    fireEvent.change(await screen.findByLabelText("User name"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Documents" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Review" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Admin" })).toBeNull();
    // Every call went to this origin's API.
    expect(calls.every((c) => / \/api\/v1\//.test(c))).toBe(true);
  });

  it("refuses an admin page to other roles, without calling the admin API", async () => {
    const calls = serve((path) =>
      path.endsWith("/auth/session") ? [200, { user: "vic", role: "viewer", csrf_token: "c" }] : [200, empty],
    );
    mount("/admin");
    expect(await screen.findByText(/cannot open this page/)).toBeTruthy();
    expect(calls.some((c) => c.includes("/admin/"))).toBe(false);
  });

  it("returns to the sign-in page when the session expires", async () => {
    let expired = false;
    serve((path) => {
      if (path.endsWith("/auth/session")) return [200, { user: "alice", role: "submitter", csrf_token: "c" }];
      if (expired) return unauthenticated;
      expired = true;
      return [200, empty];
    });
    mount();
    expect(await screen.findByRole("heading", { name: "Documents" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "march" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.getByText("Your session ended. Please sign in again.")).toBeTruthy());
  });
});
