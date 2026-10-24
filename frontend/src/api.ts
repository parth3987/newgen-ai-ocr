// The only way the application talks to anything: the internal API on this origin.
// The session cookie is HttpOnly (this code never sees it); changes carry the CSRF token.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string | null = null,
  ) {
    super(message);
  }
}

let csrf: string | null = null;
let onUnauthenticated: () => void = () => {};

export function setCsrf(token: string | null): void {
  csrf = token;
}

export function setUnauthenticatedHandler(handler: () => void): void {
  onUnauthenticated = handler;
}

const SAFE = new Set(["GET", "HEAD"]);

async function errorOf(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code: string; message: string; request_id?: string } };
    if (body.error) {
      return new ApiError(response.status, body.error.code, body.error.message, body.error.request_id ?? null);
    }
  } catch {
    // not JSON
  }
  return new ApiError(response.status, "HTTP_ERROR", response.statusText || `HTTP ${response.status}`);
}

export async function request(
  path: string,
  options: { method?: string; json?: unknown; form?: FormData; headers?: Record<string, string> } = {},
): Promise<Response> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json", ...(options.headers ?? {}) };
  if (!SAFE.has(method) && csrf) headers["X-CSRF-Token"] = csrf;
  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  } else if (options.form) {
    body = options.form;
  }
  const response = await fetch(path, { method, headers, body, credentials: "same-origin" });
  if (!response.ok) {
    const error = await errorOf(response);
    if (response.status === 401 && !path.endsWith("/auth/login")) onUnauthenticated();
    throw error;
  }
  return response;
}

export async function get<T>(path: string): Promise<T> {
  return (await (await request(path)).json()) as T;
}

export async function post<T>(path: string, json?: unknown, headers?: Record<string, string>): Promise<T> {
  const response = await request(path, { method: "POST", json: json ?? {}, headers });
  return (response.status === 204 ? null : await response.json()) as T;
}

export async function patch<T>(path: string, json: unknown): Promise<T> {
  return (await (await request(path, { method: "PATCH", json })).json()) as T;
}

export function query(params: Record<string, string | number | boolean | null | undefined | string[]>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, v));
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export interface UploadProgress {
  loaded: number;
  total: number;
}

/** Upload with progress (fetch cannot report upload progress). */
export function upload<T>(
  path: string,
  form: FormData,
  headers: Record<string, string>,
  onProgress: (p: UploadProgress) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.setRequestHeader("Accept", "application/json");
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => onProgress({ loaded: e.loaded, total: e.total });
    xhr.onerror = () => reject(new ApiError(0, "NETWORK_ERROR", "The server could not be reached"));
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // not JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as T);
        return;
      }
      const error = (body as { error?: { code: string; message: string; request_id?: string } } | null)?.error;
      if (xhr.status === 401) onUnauthenticated();
      reject(new ApiError(xhr.status, error?.code ?? "HTTP_ERROR", error?.message ?? `HTTP ${xhr.status}`, error?.request_id ?? null));
    };
    xhr.send(form);
  });
}

export function describe(error: unknown): string {
  if (error instanceof ApiError) {
    const ref = error.requestId ? ` (reference ${error.requestId.slice(0, 12)})` : "";
    return `${error.message}${ref}`;
  }
  return error instanceof Error ? error.message : String(error);
}
