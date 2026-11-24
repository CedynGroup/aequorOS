/** Month grids for the filing calendar (Monday-first, six weeks, as designed). */

const MONTH = /^(\d{4})-(\d{2})$/;
const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function isMonth(value: string | undefined | null): value is string {
  if (!value) return false;
  const match = MONTH.exec(value);
  if (!match) return false;
  const month = Number(match[2]);
  return month >= 1 && month <= 12;
}

export function monthOf(date: Date): string {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function addMonths(month: string, delta: number): string {
  const match = MONTH.exec(month);
  if (!match) throw new Error(`Not a month: ${month}`);
  const d = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1 + delta, 1));
  return monthOf(d);
}

export type GridCell = {
  date: string;
  day: number;
  inMonth: boolean;
  /** "Oct 26" on the grid's first cell and every 1st of a month, else "26". */
  label: string;
};

export function monthGrid(month: string): GridCell[] {
  const match = MONTH.exec(month);
  if (!match) throw new Error(`Not a month: ${month}`);
  const year = Number(match[1]);
  const index = Number(match[2]) - 1;
  const first = new Date(Date.UTC(year, index, 1));
  const offset = (first.getUTCDay() + 6) % 7; // Monday = 0
  const start = Date.UTC(year, index, 1 - offset);
  const cells: GridCell[] = [];
  for (let i = 0; i < 42; i += 1) {
    const d = new Date(start + i * 86_400_000);
    const day = d.getUTCDate();
    const label = i === 0 || day === 1 ? `${MONTHS_SHORT[d.getUTCMonth()]} ${day}` : String(day);
    cells.push({
      date: `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
      day,
      inMonth: d.getUTCMonth() === index,
      label,
    });
  }
  return cells;
}
