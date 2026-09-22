import { describe, expect, it } from "vitest";
import { getAdvanceForHabit, getCompletionCount, isDailySpurtComplete } from "../shared/quest";

describe("Turtle Quest daily routine scoring", () => {
  it("awards 5m for the first habit and 10m for the second habit", () => {
    const empty = { devotional: false, exercise: false } as const;
    expect(getAdvanceForHabit(empty, "devotional")).toBe(5);
    expect(getAdvanceForHabit({ devotional: true, exercise: false }, "exercise")).toBe(10);
  });

  it("does not award meters for a duplicate verification", () => {
    expect(getAdvanceForHabit({ devotional: true, exercise: false }, "devotional")).toBe(0);
  });

  it("recognizes a daily spurt only when both habits are complete", () => {
    expect(isDailySpurtComplete({ devotional: true, exercise: true })).toBe(true);
    expect(isDailySpurtComplete({ devotional: true, exercise: false })).toBe(false);
    expect(getCompletionCount({ devotional: true, exercise: false })).toBe(1);
    expect(getCompletionCount({ devotional: true, exercise: true })).toBe(2);
  });
});
