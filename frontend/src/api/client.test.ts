import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, buildQuery, parseError, request, setToken } from "./client";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => setToken(null));
afterEach(() => vi.unstubAllGlobals());

describe("parseError", () => {
  it("exposes the server's code, message and extras", async () => {
    const err = await parseError(
      json(409, {
        error: { code: "already_claimed", message: "Bob already owns this item.", current_owner: { id: 2, name: "Bob" } },
      }),
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("already_claimed");
    expect(err.currentOwner).toEqual({ id: 2, name: "Bob" });
    expect(err.isConflict).toBe(true);
    expect(err.isVersionConflict).toBe(false);
  });

  it("surfaces the fresh item on a version conflict so the client can reconcile", async () => {
    const err = await parseError(
      json(409, { error: { code: "version_conflict", message: "changed", current: { id: 7, version: 4 } } }),
    );
    expect(err.isVersionConflict).toBe(true);
    expect(err.current).toEqual({ id: 7, version: 4 });
  });

  it("handles FastAPI validation errors and non-JSON bodies", async () => {
    const v = await parseError(json(422, { detail: [{ msg: "Field required" }] }));
    expect(v.code).toBe("validation_error");
    expect(v.message).toBe("Field required");
    const html = await parseError(new Response("<html>boom</html>", { status: 502, statusText: "Bad Gateway" }));
    expect(html.status).toBe(502);
    expect(html.message).toBe("Bad Gateway");
  });
});

describe("request", () => {
  it("sends the bearer token and a JSON body", async () => {
    setToken("tok");
    const fetchMock = vi.fn().mockResolvedValue(json(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await request("/x", { method: "POST", body: { a: 1 } });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe('{"a":1}');
    expect(init.headers["Idempotency-Key"]).toBeUndefined();
  });

  it("reuses the SAME Idempotency-Key when a mutation is retried after a network failure", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network down"))
      .mockResolvedValueOnce(json(200, { done: true }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(request("/work-items/1/claim", { method: "POST", idempotent: true })).resolves.toEqual({ done: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const keys = fetchMock.mock.calls.map(([, init]) => init.headers["Idempotency-Key"]);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("uses a fresh key for each separate user action", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => json(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    await request("/a", { method: "POST", idempotent: true });
    await request("/a", { method: "POST", idempotent: true });
    const [k1, k2] = fetchMock.mock.calls.map(([, init]) => init.headers["Idempotency-Key"]);
    expect(k1).not.toBe(k2);
  });

  it("does not retry on HTTP errors (a 409 is an answer, not a failure)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(409, { error: { code: "invalid_transition", message: "no" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(request("/t", { method: "POST", idempotent: true })).rejects.toMatchObject({ status: 409 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("gives up with a clear error when the network stays down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("down")));
    await expect(request("/x", { method: "POST", idempotent: true })).rejects.toMatchObject({ code: "network_error" });
  });
});

describe("buildQuery", () => {
  it("skips empty values and repeats array params", () => {
    expect(buildQuery({ q: "", a: undefined, status: ["open", "blocked"], limit: 25, flag: true })).toBe(
      "?status=open&status=blocked&limit=25&flag=true",
    );
    expect(buildQuery({})).toBe("");
  });
});
