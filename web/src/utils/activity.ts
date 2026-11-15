import type { ActivityCost } from '../types';
import { parseCurrency } from '../utils';

export const MINUTES_PER_DAY = 1440;

type CostEntry = {
  category: ActivityCost['category'];
  value: string;
  basis: ActivityCost['basis'];
};

function parseCost(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const amount = parseCurrency(trimmed);
  if (amount === null) throw new Error('费用金额无法识别，请填写元或留空');
  return amount;
}

// 地点、金额与计费方式都未改变的类别保留全部原费用记录，含依据 ID 与未知状态；
// 表单只呈现每类第一条，因此用第一条判断该类是否变动，任一不符时按新输入重新核验。
// 已有该类费用但金额留空时保留为待确认，避免静默去掉费用。
export function editableCosts(
  original: ActivityCost[],
  samePlace: boolean,
  entries: CostEntry[],
): ActivityCost[] {
  return entries.flatMap(({ category, value, basis }) => {
    const amount = parseCost(value);
    const group = original.filter((cost) => cost.category === category);
    const first = group[0];
    if (
      samePlace &&
      first &&
      first.basis === basis &&
      (first.amount_cents ?? null) === amount
    ) {
      return group;
    }
    if (amount !== null) return [{ category, amount_cents: amount, basis, evidence_id: null }];
    // 原已有该类费用但金额留空时保留为待确认，不静默去掉费用。
    return group.length ? [{ category, amount_cents: null, basis, evidence_id: null }] : [];
  });
}

// 结束时间按时段偏移到当天、次日或后日零点，需晚于开始且不晚于后日零点。
export function activityEndMinute(
  startMinute: number,
  endMinute: number,
  endDay: number,
): number | null {
  if (!Number.isInteger(startMinute) || startMinute < 0 || startMinute >= MINUTES_PER_DAY) {
    return null;
  }
  if (!Number.isInteger(endMinute)) return null;
  if (!Number.isInteger(endDay) || endDay < 0 || endDay > 2) return null;
  const end = endMinute + endDay * MINUTES_PER_DAY;
  if (endMinute < 0 || endMinute >= MINUTES_PER_DAY) return null;
  if (end <= startMinute || end > 2 * MINUTES_PER_DAY) return null;
  return end;
}

export function endDayOf(endMinute: number): number {
  if (!Number.isFinite(endMinute) || endMinute < 0) return 0;
  return Math.min(2, Math.floor(endMinute / MINUTES_PER_DAY));
}
