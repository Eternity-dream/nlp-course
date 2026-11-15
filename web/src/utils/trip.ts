import type { TimeConstraint, Trip } from '../types';
import { parseMinute, parseCurrency } from '../utils';

export type ConstraintDraft = {
  kind: TimeConstraint['kind'];
  on_date: string;
  start: string;
  end: string;
  endDay: number;
  place: string;
};

export const CONSTRAINT_KIND_LABELS: Record<TimeConstraint['kind'], string> = {
  arrival: '抵达',
  departure: '离开',
  reservation: '预约',
};

export function emptyConstraint(date = ''): ConstraintDraft {
  return { kind: 'reservation', on_date: date, start: '', end: '', endDay: 0, place: '' };
}

export function constraintToDraft(item: TimeConstraint): ConstraintDraft {
  const end = item.end_minute ?? null;
  const endMinute = end !== null ? end % 1440 : 0;
  return {
    kind: item.kind,
    on_date: item.on_date,
    start: minuteText(item.minute),
    end: end !== null ? minuteText(endMinute) : '',
    endDay: end !== null ? Math.floor(end / 1440) : 0,
    place: item.place_name ?? '',
  };
}

function minuteText(minute: number): string {
  const h = Math.floor(minute / 60);
  const m = minute % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

// 同行人数只接受 1–1000 的十进制整数，避免 1e2、1.5 等写法被截断后按错误人数保存。
export function parseTravelers(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (!/^\d+$/.test(trimmed)) throw new Error('同行人数需要填写整数');
  const count = Number(trimmed);
  if (!Number.isInteger(count) || count < 1 || count > 1000) {
    throw new Error('同行人数需要为 1 至 1000 的整数');
  }
  return count;
}

// 空预算表示不设上限；0 是有效上限，不能当作未填写。
export function parseBudget(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const cents = parseCurrency(trimmed);
  if (cents === null) throw new Error('预算金额格式错误');
  return cents;
}

export type TripDates = Pick<Trip, 'start_date' | 'end_date'>;

// 一行草稿转换为后端时间约束；空行返回 null，非法输入抛出中文原因。
export function buildTimeConstraint(
  draft: ConstraintDraft,
  trip?: TripDates,
): TimeConstraint | null {
  const hasInput = draft.on_date || draft.start || draft.end || draft.place;
  if (!hasInput) return null;
  if (!draft.on_date) throw new Error('时间安排需要填写日期');
  const start = parseMinute(draft.start);
  if (start === null) throw new Error('时间安排需要填写开始时间');
  const end = draft.end.trim() ? parseMinute(draft.end) : null;
  if (draft.end.trim() && end === null) throw new Error('结束时间格式错误');
  const endMinute = end === null ? null : end + draft.endDay * 1440;
  if (endMinute !== null && endMinute > 2880) {
    throw new Error('结束时间不能晚于后日零点');
  }
  if (endMinute !== null && endMinute <= start) {
    throw new Error('结束时间需要晚于开始时间');
  }
  const place = draft.place.trim();
  if (draft.kind === 'reservation' && !place) throw new Error('预约需要填写地点');
  if (trip?.start_date && trip.end_date && (draft.on_date < trip.start_date || draft.on_date > trip.end_date)) {
    throw new Error('时间安排日期需要在行程日期内');
  }
  return {
    kind: draft.kind,
    on_date: draft.on_date,
    minute: start,
    ...(endMinute !== null ? { end_minute: endMinute } : {}),
    ...(place ? { place_name: place } : {}),
  };
}

export function buildTimeConstraints(drafts: ConstraintDraft[], trip?: TripDates): TimeConstraint[] {
  return drafts.flatMap((draft) => {
    const constraint = buildTimeConstraint(draft, trip);
    return constraint ? [constraint] : [];
  });
}
