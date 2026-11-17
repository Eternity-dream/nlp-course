import { describe, expect, it } from 'vitest';
import { progressEstimate } from '../utils/progress';

describe('预计进度', () => {
  it('优先使用服务端进度，并限制在百分比范围', () => {
    expect(progressEstimate({ status: 'running', progress_estimate: 88 })).toBe(88);
    expect(progressEstimate({ status: 'running', progress_estimate: 140 })).toBe(99);
  });
  it('只有成功才显示完成，旧版本缺字段时显示起始进度', () => {
    expect(progressEstimate({ status: 'running' })).toBe(0);
    expect(progressEstimate({ status: 'succeeded', progress_estimate: 88 })).toBe(100);
  });
});
