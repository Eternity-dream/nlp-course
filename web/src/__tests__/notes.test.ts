import { describe, expect, it } from 'vitest';
import { tripNotes, updateTripNotes } from '../utils/notes';

describe('补充说明', () => {
  const trip = {
    destination: '西安', requirements: ['不吃辣'], priorities: ['历史文化'],
    known_conditions: ['住南稍门附近'],
  };
  it('合并显示旧字段，保存时保留原分类和新说明', () => {
    expect(tripNotes(trip)).toEqual(['不吃辣', '历史文化', '住南稍门附近']);
    expect(updateTripNotes(tripNotes(trip).join('\n') + '\n下午两点到', trip)).toEqual({
      requirements: ['不吃辣'], priorities: ['历史文化'],
      known_conditions: ['住南稍门附近', '下午两点到'],
    });
  });
  it('允许移除旧条目并清空说明', () => {
    expect(updateTripNotes('历史文化', trip)).toEqual({
      requirements: [], priorities: ['历史文化'], known_conditions: [],
    });
    expect(updateTripNotes('', trip)).toEqual({requirements: [], priorities: [], known_conditions: []});
  });
  it('新说明去重并校验每段长度', () => {
    expect(updateTripNotes(' 想去兵马俑\n想去兵马俑 ')).toEqual({
      requirements: [], priorities: [], known_conditions: ['想去兵马俑'],
    });
    expect(() => updateTripNotes('文'.repeat(501))).toThrow('每段补充说明最多 500 字');
  });
});
