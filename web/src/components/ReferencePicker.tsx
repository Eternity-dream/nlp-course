import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api';
import type { KnowledgeSource } from '../types';
import { KnowledgeForm } from '../pages/KnowledgePage';
import { Button } from './Button';
import { formatApiError } from '../utils/errors';
import './ClarificationDialog.css';
import './ReferencePicker.css';

const labels: Record<KnowledgeSource['status'], string> = {
  pending: '处理中', processing: '处理中', ready: '已添加', failed: '处理失败',
};

export function ReferenceList({ ids, onRemove }: { ids: string[]; onRemove?: (id: string) => void }) {
  const [sources, setSources] = useState<Record<string, KnowledgeSource>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [retryError, setRetryError] = useState('');
  const key = JSON.stringify(ids);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const selected: string[] = JSON.parse(key);
    async function poll() {
      const results = await Promise.allSettled(selected.map((id) => api.getKnowledge(id)));
      if (!active) return;
      const next: Record<string, KnowledgeSource> = {};
      const failures: Record<string, string> = {};
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') next[selected[index]] = result.value;
        else failures[selected[index]] = formatApiError(result.reason);
      });
      setSources(next);
      setErrors(failures);
      timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [key]);

  return <section className="reference-list" aria-label="参考资料">
    {ids.length > 0 && <span className="input-label">参考资料</span>}
    {retryError && <p role="alert">{retryError}</p>}
    {ids.map((id) => <div className="reference-row" key={id}>
      <div className="reference-name">
        {sources[id] ? <a href={api.getOriginalUrl(id)} target="_blank" rel="noreferrer">{sources[id].title}</a> : <span>参考资料</span>}
        <span className="reference-status">{errors[id] || (sources[id] ? labels[sources[id].status] : '加载中')}</span>
        {sources[id]?.error && <span className="reference-status">{sources[id].error?.message}</span>}
      </div>
      {sources[id]?.status === 'failed' && <Button type="button" size="sm" variant="secondary" onClick={async () => {
        try { setRetryError(''); await api.retryKnowledge(id); }
        catch (err) { setRetryError(formatApiError(err)); }
      }}>重试</Button>}
      {onRemove && <Button type="button" size="sm" variant="secondary" aria-label={`移除${sources[id]?.title ?? '参考资料'}`} onClick={() => onRemove(id)}>移除</Button>}
    </div>)}
  </section>;
}

export function ReferencePicker({ ids, onChange }: { ids: string[]; onChange: (ids: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [existing, setExisting] = useState(false);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  async function loadExisting() {
    setExisting(true);
    setLoading(true);
    setError('');
    try { setSources(await api.listKnowledge(100)); }
    catch (err) { setError(formatApiError(err)); }
    finally { setLoading(false); }
  }
  return <div className="reference-picker">
    <ReferenceList ids={ids} onRemove={(id) => onChange(ids.filter((item) => item !== id))} />
    <Button type="button" variant="secondary" onClick={() => { setExisting(false); setOpen(true); }}>＋ 添加参考</Button>
    {createPortal(<dialog ref={dialog} className="clarification-dialog reference-dialog" aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); if (!busy) setOpen(false); }}>
      <div className="reference-heading"><h2 id={titleId}>添加参考</h2><Button type="button" variant="secondary" disabled={busy} onClick={() => setOpen(false)}>关闭</Button></div>
      <div className="reference-tabs">
        <Button type="button" disabled={busy} variant={existing ? 'secondary' : 'primary'} onClick={() => setExisting(false)}>新资料</Button>
        <Button type="button" disabled={busy} variant={existing ? 'primary' : 'secondary'} onClick={loadExisting}>已有资料</Button>
      </div>
      {existing ? <div className="reference-options">
        {loading && <p role="status">加载中</p>}
        {error && <p role="alert">{error}<Button type="button" onClick={loadExisting}>重试</Button></p>}
        {!loading && !error && !sources.length && <p>暂无资料</p>}
        {sources.map((source) => <label className="reference-row" key={source.id}>
          <input type="checkbox" checked={ids.includes(source.id)} onChange={(event) => onChange(event.target.checked ? [...new Set([...ids, source.id])] : ids.filter((id) => id !== source.id))} />
          <span className="reference-name">{source.title}</span>{source.status !== 'ready' && <span>{labels[source.status]}</span>}
        </label>)}
        <Button type="button" onClick={() => setOpen(false)}>完成</Button>
      </div> : open && <KnowledgeForm onBusyChange={setBusy} onCancel={() => setOpen(false)} onSuccess={(source) => {
        onChange([...new Set([...ids, source.id])]); setOpen(false);
      }} />}
    </dialog>, document.body)}
  </div>;
}
