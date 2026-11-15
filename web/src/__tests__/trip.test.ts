import { describe, expect, it } from 'vitest';
import {
  buildTimeConstraint,
  buildTimeConstraints,
  constraintToDraft,
  emptyConstraint,
  parseBudget,
  parseTravelers,
} from '../utils/trip';
import { safeExternalUrl } from '../utils/url';

describe('parseTravelers', () => {
  it('接受正整数', () => {
    expect(parseTravelers('2')).toBe(2);
    expect(parseTravelers(' 10 ')).toBe(10);
    expect(parseTravelers('1000')).toBe(1000);
  });

  it('空值视为未填写', () => {
    expect(parseTravelers('')).toBeNull();
    expect(parseTravelers('   ')).toBeNull();
  });

  it('拒绝科学计数法、小数与超范围人数', () => {
    expect(() => parseTravelers('1e2')).toThrow('同行人数需要填写整数');
    expect(() => parseTravelers('1.5')).toThrow('同行人数需要填写整数');
    expect(() => parseTravelers('0')).toThrow('同行人数需要为 1 至 1000 的整数');
    expect(() => parseTravelers('1001')).toThrow('同行人数需要为 1 至 1000 的整数');
  });
});

describe('parseBudget', () => {
  it('零预算保留为零', () => {
    expect(parseBudget('0')).toBe(0);
    expect(parseBudget('0.00')).toBe(0);
  });

  it('空预算表示不设上限', () => {
    expect(parseBudget('')).toBeNull();
  });

  it('拒绝非法金额', () => {
    expect(() => parseBudget('三千')).toThrow('预算金额格式错误');
    expect(() => parseBudget('-1')).toThrow('预算金额格式错误');
    expect(() => parseBudget('1e3')).toThrow('预算金额格式错误');
    expect(() => parseBudget('12.345')).toThrow('预算金额格式错误');
  });
});

describe('buildTimeConstraint', () => {
  const trip = { start_date: '2026-12-01', end_date: '2026-12-03' };

  it('空行不生成约束', () => {
    expect(buildTimeConstraint(emptyConstraint(), trip)).toBeNull();
  });

  it('抵达时间只需开始时间', () => {
    expect(
      buildTimeConstraint(
        { kind: 'arrival', on_date: '2026-12-01', start: '11:20', end: '', endDay: 0, place: '' },
        trip
      )
    ).toEqual({ kind: 'arrival', on_date: '2026-12-01', minute: 680 });
  });

  it('预约保留地点与可选结束时间', () => {
    expect(
      buildTimeConstraint(
        {
          kind: 'reservation',
          on_date: '2026-12-02',
          start: '10:00',
          end: '11:00',
          endDay: 0,
          place: '碑林博物馆',
        },
        trip
      )
    ).toEqual({
      kind: 'reservation',
      on_date: '2026-12-02',
      minute: 600,
      end_minute: 660,
      place_name: '碑林博物馆',
    });
  });

  it('次日结束按 1440 偏移', () => {
    expect(
      buildTimeConstraint(
        { kind: 'departure', on_date: '2026-12-03', start: '23:30', end: '00:30', endDay: 1, place: '' },
        trip
      )
    ).toEqual({ kind: 'departure', on_date: '2026-12-03', minute: 1410, end_minute: 1470 });
  });

  it('拒绝结束早于开始与超出后日零点', () => {
    expect(() =>
      buildTimeConstraint(
        { kind: 'arrival', on_date: '2026-12-01', start: '12:00', end: '11:00', endDay: 0, place: '' },
        trip
      )
    ).toThrow('结束时间需要晚于开始时间');
    expect(() =>
      buildTimeConstraint(
        { kind: 'arrival', on_date: '2026-12-01', start: '12:00', end: '23:00', endDay: 2, place: '' },
        trip
      )
    ).toThrow('结束时间不能晚于后日零点');
  });

  it('预约缺少地点与日期越界时拒绝', () => {
    expect(() =>
      buildTimeConstraint(
        { kind: 'reservation', on_date: '2026-12-01', start: '10:00', end: '', endDay: 0, place: '' },
        trip
      )
    ).toThrow('预约需要填写地点');
    expect(() =>
      buildTimeConstraint(
        { kind: 'arrival', on_date: '2026-12-05', start: '10:00', end: '', endDay: 0, place: '' },
        trip
      )
    ).toThrow('时间安排日期需要在行程日期内');
    expect(() =>
      buildTimeConstraint(
        { kind: 'arrival', on_date: '', start: '10:00', end: '', endDay: 0, place: '' },
        trip
      )
    ).toThrow('时间安排需要填写日期');
  });
});

describe('constraintToDraft', () => {
  it('还原后端约束到表单草稿', () => {
    expect(
      constraintToDraft({ kind: 'departure', on_date: '2026-12-03', minute: 1410, end_minute: 1470 })
    ).toEqual({
      kind: 'departure',
      on_date: '2026-12-03',
      start: '23:30',
      end: '00:30',
      endDay: 1,
      place: '',
    });
  });

  it('无结束时间时保持空值', () => {
    const draft = constraintToDraft({ kind: 'arrival', on_date: '2026-12-01', minute: 600 });
    expect(draft.end).toBe('');
    expect(draft.endDay).toBe(0);
  });

  it('往返后约束保持一致', () => {
    const constraint = {
      kind: 'reservation' as const,
      on_date: '2026-12-02',
      minute: 600,
      end_minute: 2000,
      place_name: '场馆',
    };
    expect(buildTimeConstraint(constraintToDraft(constraint))).toEqual(constraint);
  });
});

describe('buildTimeConstraints', () => {
  it('保留已有约束并跳过空行', () => {
    expect(
      buildTimeConstraints(
        [
          constraintToDraft({ kind: 'arrival', on_date: '2026-12-01', minute: 600 }),
          emptyConstraint(),
          constraintToDraft({ kind: 'departure', on_date: '2026-12-03', minute: 900 }),
        ],
        { start_date: '2026-12-01', end_date: '2026-12-03' }
      )
    ).toHaveLength(2);
  });
});

describe('safeExternalUrl', () => {
  it('只允许 http 与 https', () => {
    expect(safeExternalUrl('https://example.com/a')).toBe('https://example.com/a');
    expect(safeExternalUrl('http://example.com')).toBe('http://example.com/');
    expect(safeExternalUrl('javascript:alert(1)')).toBeNull();
    expect(safeExternalUrl('data:text/html,hi')).toBeNull();
    expect(safeExternalUrl('')).toBeNull();
    expect(safeExternalUrl(null)).toBeNull();
    expect(safeExternalUrl('非链接')).toBeNull();
  });
});
