export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} hr ago`;
  if (seconds < 86400 * 30) {
    const d = Math.round(seconds / 86400);
    return `${d} day${d === 1 ? "" : "s"} ago`;
  }
  return new Date(iso).toLocaleDateString();
}

export function formatDue(iso: string | null, now: number = Date.now()): { text: string; overdue: boolean } {
  if (!iso) return { text: "No due date", overdue: false };
  const t = new Date(iso).getTime();
  return { text: new Date(iso).toLocaleDateString(), overdue: t < now };
}

/** <input type="datetime-local"> value (local time) from an ISO string, and back. */
export function isoToLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function localInputToIso(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}
