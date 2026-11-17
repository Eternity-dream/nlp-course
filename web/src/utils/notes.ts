import type { TripFields } from '../types';

const fields = ['requirements', 'priorities', 'known_conditions'] as const;

export function tripNotes(trip?: TripFields): string[] {
  return [...new Set(fields.flatMap((field) => trip?.[field] ?? []))];
}

export function updateTripNotes(text: string, trip?: TripFields) {
  const lines = [...new Set(text.split('\n').map((line) => line.trim()).filter(Boolean))];
  if (lines.some((line) => line.length > 500)) throw new Error('每段补充说明最多 500 字，请分段填写');
  if (lines.length > 100) throw new Error('补充说明最多 100 段');
  // 保留既有条目的分类，新输入由规划器按原文判断限制、偏好与背景。
  return {
    requirements: lines.filter((line) => trip?.requirements?.includes(line)),
    priorities: lines.filter((line) => trip?.priorities?.includes(line)),
    known_conditions: lines.filter((line) =>
      !trip?.requirements?.includes(line) && !trip?.priorities?.includes(line)),
  };
}
