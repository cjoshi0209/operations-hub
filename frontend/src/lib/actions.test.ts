import { describe, expect, it } from "vitest";
import { can, changedUnderneath, describeEvent, reasonLabel, transitionTargets } from "./actions";

const item = (allowed_actions: string[]) => ({ allowed_actions });

describe("allowed_actions helpers (the UI never decides permissions)", () => {
  it("can() is a pure lookup in the server-provided list", () => {
    expect(can(item(["comment", "claim"]), "claim")).toBe(true);
    expect(can(item(["comment"]), "claim")).toBe(false);
    expect(can(item([]), "edit")).toBe(false);
  });

  it("transitionTargets() extracts targets in server order", () => {
    expect(transitionTargets(item(["comment", "transition:in_progress", "edit", "transition:closed"]))).toEqual([
      "in_progress",
      "closed",
    ]);
    expect(transitionTargets(item(["comment"]))).toEqual([]);
  });
});

describe("changedUnderneath", () => {
  it("only fires when the server is ahead of what the user has seen", () => {
    expect(changedUnderneath(5, 3)).toBe(true);
    expect(changedUnderneath(3, 3)).toBe(false);
    expect(changedUnderneath(3, 5)).toBe(false);
    expect(changedUnderneath(undefined, 3)).toBe(false);
    expect(changedUnderneath(4, undefined)).toBe(false);
  });
});

describe("describeEvent", () => {
  it("renders timeline sentences", () => {
    expect(describeEvent("status_changed", { from: "open", to: "in_progress" })).toBe(
      "moved it from Open to In progress",
    );
    expect(describeEvent("status_changed", { from: "resolved", to: "closed", resolution_note: "done" })).toContain(
      "done",
    );
    expect(describeEvent("claimed", {})).toBe("took ownership");
    expect(describeEvent("updated", { changes: { due_at: {}, assignee_id: {} } })).toBe("edited due at, assignee");
    expect(describeEvent("something_new", {})).toBe("something new");
  });
});

describe("labels", () => {
  it("falls back to the raw key for unknown reasons", () => {
    expect(reasonLabel("assigned_to_me")).toBe("Assigned to me");
    expect(reasonLabel("mystery")).toBe("mystery");
  });
});
