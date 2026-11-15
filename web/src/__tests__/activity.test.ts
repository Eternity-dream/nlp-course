import { describe, expect, it } from 'vitest';
import { activityEndMinute, editableCosts, endDayOf } from '../utils/activity';
import type { ActivityCost } from '../types';

const ticketWithEvidence: ActivityCost = {
  category: 'ticket',
  amount_cents: 6500,
  basis: 'per_person',
  evidence_id: 'evidence-1',
};

const unknownMeal: ActivityCost = {
  category: 'meal',
  amount_cents: null,
  basis: 'per_group',
  evidence_id: null,
};

describe('editableCosts', () => {
  it('只改时间时保留原费用依据与未知金额', () => {
    const original = [ticketWithEvidence, unknownMeal];
    const costs = editableCosts(original, true, [
      { category: 'ticket', value: '65', basis: 'per_person' },
      { category: 'meal', value: '', basis: 'per_group' },
    ]);
    expect(costs).toEqual(original);
    expect(costs[0].evidence_id).toBe('evidence-1');
  });

  it('保留了同类中的全部费用记录', () => {
    const extraTicket: ActivityCost = {
      category: 'ticket',
      amount_cents: 6500,
      basis: 'per_person',
      evidence_id: 'evidence-2',
    };
    const costs = editableCosts([ticketWithEvidence, extraTicket], true, [
      { category: 'ticket', value: '65', basis: 'per_person' },
    ]);
    expect(costs).toEqual([ticketWithEvidence, extraTicket]);
  });

  it('金额或计费方式改变后清除旧依据', () => {
    const changed = editableCosts([ticketWithEvidence], true, [
      { category: 'ticket', value: '80', basis: 'per_person' },
    ]);
    expect(changed).toEqual([
      { category: 'ticket', amount_cents: 8000, basis: 'per_person', evidence_id: null },
    ]);

    const rebased = editableCosts([ticketWithEvidence], true, [
      { category: 'ticket', value: '65', basis: 'per_group' },
    ]);
    expect(rebased).toEqual([
      { category: 'ticket', amount_cents: 6500, basis: 'per_group', evidence_id: null },
    ]);
  });

  it('换地点后不沿用旧依据', () => {
    const costs = editableCosts([ticketWithEvidence], false, [
      { category: 'ticket', value: '65', basis: 'per_person' },
    ]);
    expect(costs[0].evidence_id).toBeNull();
  });

  it('新增地点未填金额时不生成费用', () => {
    expect(
      editableCosts([], false, [{ category: 'ticket', value: '', basis: 'per_person' }])
    ).toEqual([]);
  });

  it('已有费用清空金额后保留为待确认', () => {
    expect(
      editableCosts([ticketWithEvidence], true, [
        { category: 'ticket', value: '', basis: 'per_person' },
      ])
    ).toEqual([{ category: 'ticket', amount_cents: null, basis: 'per_person', evidence_id: null }]);
  });

  it('拒绝无法识别的金额', () => {
    expect(() =>
      editableCosts([], false, [{ category: 'ticket', value: '1e2', basis: 'per_person' }])
    ).toThrow('费用金额无法识别，请填写元或留空');
  });

  it('零元费用与空金额区分处理', () => {
    const zero = editableCosts([], false, [
      { category: 'ticket', value: '0', basis: 'per_person' },
    ]);
    expect(zero).toEqual([
      { category: 'ticket', amount_cents: 0, basis: 'per_person', evidence_id: null },
    ]);
  });
});

describe('activityEndMinute', () => {
  it('当天结束保持原分钟', () => {
    expect(activityEndMinute(540, 720, 0)).toBe(720);
  });

  it('跨过午夜按次日分钟计算', () => {
    expect(activityEndMinute(1380, 60, 1)).toBe(1500);
  });

  it('第二天零点结束', () => {
    expect(activityEndMinute(1380, 0, 1)).toBe(1440);
  });

  it('拒绝无效与倒序时间', () => {
    expect(activityEndMinute(720, 720, 0)).toBeNull();
    expect(activityEndMinute(720, 600, 0)).toBeNull();
    expect(activityEndMinute(-1, 600, 0)).toBeNull();
    expect(activityEndMinute(540, 1440, 0)).toBeNull();
    expect(activityEndMinute(540, 600, 3)).toBeNull();
    expect(activityEndMinute(540.5, 600, 0)).toBeNull();
  });

  it('不超过后日零点', () => {
    expect(activityEndMinute(0, 0, 2)).toBe(2880);
    expect(activityEndMinute(60, 0, 2)).toBe(2880);
  });
});

describe('endDayOf', () => {
  it('按结束分钟判断当天或次日', () => {
    expect(endDayOf(720)).toBe(0);
    expect(endDayOf(1439)).toBe(0);
    expect(endDayOf(1440)).toBe(1);
    expect(endDayOf(1500)).toBe(1);
    expect(endDayOf(2880)).toBe(2);
  });

  it('异常输入回落到当天', () => {
    expect(endDayOf(-30)).toBe(0);
    expect(endDayOf(Number.NaN)).toBe(0);
  });
});
