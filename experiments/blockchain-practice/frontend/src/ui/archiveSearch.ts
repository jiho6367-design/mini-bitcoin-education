export interface ArchiveFilters {
  q: string;
  date: string;
}

/** A local calendar day may be 23 or 25 hours; advance the date, not milliseconds. */
export function archiveSearchParams(
  filters: ArchiveFilters,
  offset: number,
  limit = 20,
) {
  const params = new URLSearchParams({
    q: filters.q.trim(),
    limit: String(limit),
    offset: String(offset),
  });
  if (filters.date) {
    const start = new Date(`${filters.date}T00:00:00`);
    if (!Number.isFinite(start.getTime()))
      throw new Error("저장 날짜를 다시 선택하세요");
    const end = new Date(start);
    end.setDate(end.getDate() + 1);
    params.set("updatedFrom", start.toISOString());
    params.set("updatedUntil", end.toISOString());
  }
  return params;
}
