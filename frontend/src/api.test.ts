import { afterEach, describe as group, expect, it, vi } from "vitest";

import { ApiError, describe, get, post, query, request, setCsrf, setUnauthenticatedHandler } from "./api";
import { atLeast } from "./auth";

function respond(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrf(null);
});

group("api", () => {
  it("sends the CSRF token on changes only, and only to this origin's paths", async () => {
    const fetch = respond(200, {});
    vi.stubGlobal("fetch", fetch);
    setCsrf("t0k3n");
    await get("/api/v1/documents");
    await post("/api/v1/jobs/x/cancel");
    const [, read] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    const [, change] = fetch.mock.calls[1] as unknown as [string, RequestInit];
    expect((read.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
    expect((change.headers as Record<string, string>)["X-CSRF-Token"]).toBe("t0k3n");
    expect(change.credentials).toBe("same-origin");
  });

  it("turns the error envelope into a message with a reference", async () => {
    vi.stubGlobal("fetch", respond(409, { error: { code: "JOB_CONFLICT", message: "The job is already succeeded", request_id: "abcdef1234567890" } }));
    const error = await request("/api/v1/jobs/x/cancel", { method: "POST" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("JOB_CONFLICT");
    expect(describe(error)).toBe("The job is already succeeded (reference abcdef123456)");
  });

  it("signs out when the session is no longer accepted", async () => {
    const handler = vi.fn();
    setUnauthenticatedHandler(handler);
    vi.stubGlobal("fetch", respond(401, { error: { code: "UNAUTHENTICATED", message: "A valid bearer token is required" } }));
    await expect(get("/api/v1/documents")).rejects.toThrow();
    expect(handler).toHaveBeenCalledOnce();
    // A wrong password is not an expired session.
    await expect(post("/api/v1/auth/login", {})).rejects.toThrow();
    expect(handler).toHaveBeenCalledOnce();
  });

  it("builds query strings without empty values", () => {
    expect(query({ q: "a b", status: "", limit: 25, s: ["x", "y"], n: null })).toBe("?q=a+b&limit=25&s=x&s=y");
    expect(query({})).toBe("");
  });

  it("orders roles", () => {
    expect(atLeast("admin", "viewer")).toBe(true);
    expect(atLeast("submitter", "viewer")).toBe(false);
    expect(atLeast(undefined, "submitter")).toBe(false);
  });
});
