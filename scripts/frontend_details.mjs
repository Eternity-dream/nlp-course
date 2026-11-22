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
  await search.getByRole('textbox').fill('碑林');
  await search.getByRole('button', { name: '搜索', exact: true }).click();
  await page.locator('.hit-list').getByRole('button', { name: new RegExp(title) }).first().press('Enter');
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
    name: '西安笔记.txt', mimeType: 'text/plain', buffer: Buffer.from(`瓮城。西安城墙文件验收。${fileTitle}`),
  });
  await capture('desktop-file-import', true);
  await page.getByRole('button', { name: '添加', exact: true }).click();
  const files = await poll(() => api('/api/knowledge'), data => data.some(item => item.title === fileTitle && item.status === 'ready'));
  const fileId = files.find(item => item.title === fileTitle).id;
  assert((await api('/api/knowledge/search?q=瓮城')).some(hit => hit.source_id === fileId));
  await page.getByRole('button', { name: fileTitle, exact: true }).click();
  await detail.getByRole('heading', { name: fileTitle, exact: true }).waitFor();
  await detail.getByRole('button', { name: '删除资料', exact: true }).click();
  await poll(() => api('/api/knowledge'), data => !data.some(item => item.id === fileId));
  await page.goto(base + '/knowledge');
  await page.locator('.knowledge-header').getByRole('button', { name: '搜索', exact: true }).click();
  await search.getByRole('textbox').fill('瓮城');
  await search.getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByText('未找到匹配的资料', { exact: true }).waitFor();
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

export async function checkDateBounds({ page, base, api, checked }) {
  await page.goto(base);
  await page.getByRole('button', { name: '新建行程', exact: true }).first().click();
  const start = page.getByLabel('开始日期', { exact: true });
  const end = page.getByLabel('结束日期', { exact: true });
  const save = page.getByRole('button', { name: '保存', exact: true });
  await page.getByLabel('目的地', { exact: true }).fill('西安');
  assert.equal(await end.evaluate(input => {
    input.value = '2026-02-30';
    return input.value;
  }), '');
  await start.fill('2026-12-02');
  for (const date of ['2026-12-01', '2026-12-03', '2026-12-09']) {
    await end.fill(date);
    const response = page.waitForResponse(r => r.url().endsWith('/api/trips') && r.request().method() === 'POST');
    await save.click();
    assert.equal((await response).status(), 422);
    await page.locator('.trip-form-error').waitFor();
    assert.equal(await end.inputValue(), date);
  }
  await end.fill('2026-12-08');
  await save.click();
  await page.waitForURL(/\/trips\//);
  await page.reload();
  await page.getByRole('button', { name: '编辑要求', exact: true }).click();
  assert.equal(await start.inputValue(), '2026-12-02');
  assert.equal(await end.inputValue(), '2026-12-08');
  assert.equal(await page.getByLabel(/预算/).inputValue(), '');
  const trip = await api('/api/trips/' + new URL(page.url()).pathname.split('/')[2]);
  assert.equal(trip.budget_cents, null);
  checked('非法日历日期、倒序、2/8天被拒；7天及留空预算刷新读回');
}

export async function checkInlineImports({ page, base, api, poll, checked }) {
  const dialog = page.getByRole('dialog', { name: '添加参考' });
  const open = page.getByRole('button', { name: '＋ 添加参考', exact: true });
  const add = dialog.getByRole('button', { name: '添加', exact: true });
  await open.click();
  await dialog.getByRole('radio', { name: '上传文件' }).check();
  const fileName = '表单西安城墙笔记.txt';
  await dialog.locator('input[type=file]').setInputFiles({
    name: fileName, mimeType: 'text/plain', buffer: Buffer.from('永宁门的瓮城与箭楼可结合参观。'),
  });
  await add.click();
  await page.getByRole('link', { name: fileName, exact: true }).waitFor();
  await poll(() => api('/api/knowledge'), rows => rows.some(s => s.title === fileName && s.status === 'ready'));
  await page.getByRole('button', { name: '移除' + fileName, exact: true }).click();
  checked('表单上传文件、解析就绪与取消关联');

  await open.click();
  await dialog.getByRole('radio', { name: '添加链接' }).check();
  const url = 'https://www.sxhm.com/guide.html';
  const urlTitle = '陕历博参观指南';
  await dialog.getByLabel('标题（选填）').fill(urlTitle);
  await dialog.getByLabel('URL', { exact: true }).fill(url);
  // 仅第一次提交返回可重试错误，第二次使用真实的资料导入端点。
  await page.route(base + '/api/knowledge', route => route.fulfill({
    status: 503, json: { error: { message: '导入暂时不可用' } },
  }), { times: 1 });
  await add.click();
  await dialog.getByRole('alert').filter({ hasText: '导入暂时不可用' }).waitFor();
  assert.equal(await dialog.getByLabel('URL', { exact: true }).inputValue(), url);
  assert.equal(await dialog.getByLabel('标题（选填）').inputValue(), urlTitle);
  assert.equal(await page.getByLabel('目的地', { exact: true }).inputValue(), '西安');
  await add.click();
  await page.getByRole('link', { name: urlTitle, exact: true }).waitFor();
  await poll(() => api('/api/knowledge'), data => data.some(s => s.title === urlTitle && s.kind === 'url' && s.status === 'ready'));
  await page.getByRole('button', { name: '移除' + urlTitle, exact: true }).click();
  checked('表单链接导入失败后保留地址、标题与行程输入，再提交成功');
}

export async function checkFailedSource({ page, base, api, poll, checked }) {
  await page.goto(base + '/knowledge');
  await page.getByRole('button', { name: '添加资料', exact: true }).first().click();
  await page.getByRole('radio', { name: '收藏链接', exact: true }).check();
  const title = '秦始皇帝陵博物院导览';
  await page.getByLabel('标题（选填）').fill(title);
  await page.getByLabel('URL', { exact: true }).fill('https://www.bmy.com.cn/guide.html');
  await page.getByRole('button', { name: '添加', exact: true }).click();
  await poll(() => api('/api/knowledge'), rows => rows.some(s => s.title === title && s.kind === 'bookmark' && s.status === 'ready'));
  checked('参考资料页收藏链接导入并提取正文');

  await page.getByRole('button', { name: '添加资料', exact: true }).first().click();
  await page.getByRole('radio', { name: '上传文件', exact: true }).check();
  await page.locator('input[type=file]').setInputFiles({
    name: '编码异常.txt', mimeType: 'text/plain', buffer: Buffer.from([0xff, 0xfe, 0xff]),
  });
  await page.getByRole('button', { name: '添加', exact: true }).click();
  await page.getByRole('button', { name: '编码异常.txt', exact: true }).click();
  const detail = page.locator('.knowledge-detail');
  const retry = detail.getByRole('button', { name: '重新导入', exact: true });
  await retry.waitFor();
  const response = page.waitForResponse(r => r.url().endsWith('/retry') && r.request().method() === 'POST');
  await retry.click();
  assert.equal((await response).status(), 200);
  await detail.getByText(/文本需要 UTF-8 编码/).waitFor();
  await retry.waitFor();
  assert.equal(await detail.locator('.chunk-item').count(), 0);
  checked('资料解析失败显示原因，重新导入提交至后端并复核最终状态');
}
