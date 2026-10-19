import { describe, expect, it } from "vitest";
import { formatDue, isoToLocalInput, localInputToIso, timeAgo } from "./format";

const NOW = Date.parse("2026-01-10T12:00:00Z");

describe("timeAgo", () => {
  it("buckets durations", () => {
    expect(timeAgo("2026-01-10T11:59:50Z", NOW)).toBe("just now");
    expect(timeAgo("2026-01-10T11:30:00Z", NOW)).toBe("30 min ago");
    expect(timeAgo("2026-01-10T07:00:00Z", NOW)).toBe("5 hr ago");
    expect(timeAgo("2026-01-09T12:00:00Z", NOW)).toBe("1 day ago");
    expect(timeAgo("2026-01-05T12:00:00Z", NOW)).toBe("5 days ago");
  });
});

describe("formatDue", () => {
  it("flags overdue items and handles no due date", () => {
    expect(formatDue(null, NOW)).toEqual({ text: "No due date", overdue: false });
    expect(formatDue("2026-01-01T00:00:00Z", NOW).overdue).toBe(true);
    expect(formatDue("2026-02-01T00:00:00Z", NOW).overdue).toBe(false);
  });
});

describe("datetime-local helpers", () => {
  it("round-trips through the local input format", () => {
    const iso = "2026-03-04T10:30:00.000Z";
    expect(localInputToIso(isoToLocalInput(iso))).toBe(iso);
    expect(isoToLocalInput(null)).toBe("");
    expect(localInputToIso("")).toBeNull();
  });
});
