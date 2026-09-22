export type HabitKind = "devotional" | "exercise";

export type DailyHabits = Record<HabitKind, boolean>;

/** Return the meters earned by a newly completed habit today. */
export function getAdvanceForHabit(habits: DailyHabits, kind: HabitKind): number {
  if (habits[kind]) return 0;
  return habits.devotional || habits.exercise ? 10 : 5;
}

export function isDailySpurtComplete(habits: DailyHabits): boolean {
  return habits.devotional && habits.exercise;
}

export function getCompletionCount(habits: DailyHabits): number {
  return Number(habits.devotional) + Number(habits.exercise);
}
