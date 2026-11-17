import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

export async function checkKnowledgeDetails({ page, base, api, poll, capture, checked, title, sourceId }) {
  await page.getByRole('button', { name: title, exact: true }).press('Enter');
  const detail = page.locator('.knowledge-detail');
  await detail.getByRole('heading', { name: title, exact: true }).waitFor();
  assert((await detail.innerText()).includes('西安碑林博物馆'));
  assert((await detail.innerText()).includes('第 1 段'));
  const downloadEvent = page.waitForEvent('download');
  await detail.getByRole('link', { name: `下载原文：${title}`, exact: true }).click();
  const download = await downloadEvent;
  assert((await fs.readFile(await download.path(), 'utf8')).includes('西安碑林博物馆'));
  await capture('desktop-knowledge-detail', true);
  await page.setViewportSize({ width: 320, height: 844 });
  await capture('mobile-320-knowledge-detail', true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await detail.getByRole('button', { name: '返回列表', exact: true }).click();
  await page.locator('.knowledge-header').getByRole('button', { name: '搜索', exact: true }).click();
  const search = page.getByRole('search');
  await search.getByRole('textbox').fill('zzzxemptysearch');
  await search.getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByText('未找到匹配的资料', { exact: true }).waitFor();
  await search.getByRole('textbox').fill(title);
  await search.getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByRole('button', { name: `打开资料：${title}`, exact: true }).first().press('Enter');
  await detail.locator('.chunk-item.highlight').waitFor();
  assert((await detail.locator('.chunk-item.highlight').innerText()).includes('西安碑林博物馆'));
  await capture('desktop-knowledge-search', true);
  checked('资料详情、段落定位、原文下载、搜索命中与空结果');

  await page.goto(base + '/knowledge');
  await page.getByRole('button', { name: '添加资料', exact: true }).first().click();
  await page.getByRole('radio', { name: '上传文件', exact: true }).check();
  const fileTitle = `文件导入 ${Date.now()}`;
  await page.getByLabel('标题（选填）', { exact: true }).fill(fileTitle);
  await page.locator('input[type=file]').setInputFiles({
    name: '西安笔记.txt', mimeType: 'text/plain', buffer: Buffer.from(`西安城墙文件验收。${fileTitle}`),
  });
  await capture('desktop-file-import', true);
  await page.getByRole('button', { name: '添加', exact: true }).click();
  const files = await poll(() => api('/api/knowledge'), data => data.some(item => item.title === fileTitle && item.status === 'ready'));
  const fileId = files.find(item => item.title === fileTitle).id;
  await page.getByRole('button', { name: fileTitle, exact: true }).click();
  await detail.getByRole('heading', { name: fileTitle, exact: true }).waitFor();
  await detail.getByRole('button', { name: '删除资料', exact: true }).click();
  await poll(() => api('/api/knowledge'), data => !data.some(item => item.id === fileId));
  assert.equal((await api(`/api/knowledge/search?q=${encodeURIComponent(fileTitle)}`)).some(hit => hit.source_id === fileId), false);
  checked('文件导入、详情删除与检索清理');

  // 分页边界用固定列表响应，避免为每次浏览器回归占用 100 份资料容量。
  const source = await api(`/api/knowledge/${sourceId}`);
  const entries = Array.from({ length: 21 }, (_, index) => ({ ...source, id: `page-${index}`, title: `分页资料 ${index + 1}` }));
  const listing = url => url.pathname === '/api/knowledge';
  await page.route(listing, route => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get('offset') || 0);
    const limit = Number(url.searchParams.get('limit') || 20);
    return route.fulfill({ json: entries.slice(offset, offset + limit) });
  });
  await page.goto(base + '/knowledge');
  await page.getByRole('button', { name: '分页资料 20', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '分页资料 21', exact: true }).count(), 0);
  await page.getByRole('button', { name: '加载更多', exact: true }).click();
  await page.getByRole('button', { name: '分页资料 21', exact: true }).waitFor();
  assert.equal(await page.locator('.knowledge-item-title').count(), 21);
  await page.unroute(listing);
  checked('资料列表加载更多');
}

// 扩展验收通过实际表单保存，随后从 HTTP 接口核对字段与版本。
export async function checkTimeConstraints({ page, base, api, capture, checked }) {
  await page.goto(base + '/');
  await page.getByRole('button', { name: '新建行程', exact: true }).first().click();
  await page.getByLabel('目的地', { exact: true }).fill('西安');
  await page.getByLabel('开始日期', { exact: true }).fill('2026-12-01');
  await page.getByLabel('结束日期', { exact: true }).fill('2026-12-03');
  await page.getByLabel('同行人数', { exact: true }).fill('2');
  await page.getByLabel(/预算/).fill('0');
  const rows = [
    { kind: 'arrival', date: '2026-12-01', start: '08:00', place: '西安站' },
    { kind: 'departure', date: '2026-12-03', start: '21:00', place: '西安站' },
    { kind: 'reservation', date: '2026-12-02', start: '23:30', end: '01:00', place: '公园' },
  ];
  for (const [index, row] of rows.entries()) {
    await page.locator('.trip-form-section').getByRole('button', { name: '添加', exact: true }).click();
    const group = page.getByRole('group', { name: `时间安排 ${index + 1}`, exact: true });
    await group.getByLabel('类型', { exact: true }).selectOption(row.kind);
    await group.getByLabel('日期', { exact: true }).fill(row.date);
    await group.getByLabel('开始时间', { exact: true }).fill(row.start);
    await group.getByLabel('地点', { exact: true }).fill(row.place);
    if (row.end) {
      await group.getByLabel('结束时间', { exact: true }).fill(row.end);
      await group.getByLabel('结束日期', { exact: true }).selectOption('1');
    }
  }
  await capture('desktop-time-constraints', true);
  await page.setViewportSize({ width: 320, height: 844 });
  await capture('mobile-320-time-constraints', true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.waitForURL(/\/trips\//);
  const id = new URL(page.url()).pathname.split('/')[2];
  const saved = await api(`/api/trips/${id}`);
  assert.equal(saved.budget_cents, 0);
  assert.deepEqual(saved.time_constraints, [
    { kind: 'arrival', on_date: '2026-12-01', minute: 480, end_minute: null, place_name: '西安站' },
    { kind: 'departure', on_date: '2026-12-03', minute: 1260, end_minute: null, place_name: '西安站' },
    { kind: 'reservation', on_date: '2026-12-02', minute: 1410, end_minute: 1500, place_name: '公园' },
  ]);
  await page.getByRole('button', { name: '编辑要求', exact: true }).click();
  assert.equal(await page.getByLabel(/预算/).inputValue(), '0');
  const reservation = page.getByRole('group', { name: '时间安排 3', exact: true });
  assert.equal(await reservation.getByLabel('结束时间', { exact: true }).inputValue(), '01:00');
  assert.equal(await reservation.getByLabel('结束日期', { exact: true }).inputValue(), '1');
  await page.getByLabel('补充说明（选填）', { exact: true }).fill('住在钟楼附近');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.getByRole('button', { name: '编辑要求', exact: true }).waitFor();
  assert.deepEqual((await api(`/api/trips/${id}`)).time_constraints, saved.time_constraints);
  await page.getByRole('button', { name: '编辑要求', exact: true }).click();
  await page.getByRole('button', { name: '删除时间安排 3', exact: true }).click();
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.getByRole('button', { name: '编辑要求', exact: true }).waitFor();
  assert.equal((await api(`/api/trips/${id}`)).time_constraints.length, 2);
  checked('抵达、离开、跨午夜预约、重开保留、删除和零预算');
}
