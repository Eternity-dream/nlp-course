import { useEffect, useId, useRef, useState } from 'react';
import { api } from '../api';
import type { PlanJob, Trip } from '../types';
import { formatApiError } from '../utils/errors';
import { parseBudget, parseTravelers } from '../utils/trip';
import { Button } from './Button';
import { Input, Textarea } from './Input';
import './ClarificationDialog.css';

interface Props {
  trip: Trip;
  job: PlanJob;
  open: boolean;
  onClose: () => void;
  onComplete: () => Promise<void>;
}

export function ClarificationDialog({ trip, job, open, onClose, onComplete }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const busy = useRef(false);
  const [answer, setAnswer] = useState('');
  const [startDate, setStartDate] = useState(trip.start_date ?? '');
  const [endDate, setEndDate] = useState(trip.end_date ?? '');
  const [travelers, setTravelers] = useState(trip.travelers?.toString() ?? '');
  const [initialBudget] = useState(trip.budget_cents == null ? '' : String(trip.budget_cents / 100));
  const [budget, setBudget] = useState(initialBudget);
  const [missing] = useState(trip.missing_fields);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const budgetQuestion = job.questions.some((question) => /预算|费用|金额/.test(question));

  useEffect(() => {
    const element = dialog.current;
    if (open && !element?.open) element?.showModal();
    if (!open && element?.open) element.close();
  }, [open]);

  async function submit(text: string) {
    if (busy.current) return;
    if (!text.trim() && !missing.length && budget === initialBudget) {
      setError('请填写补充内容，或选择按推荐安排');
      return;
    }
    busy.current = true;
    setSaving(true);
    setError('');
    try {
      const currentJob = await api.getJob(job.id);
      if (currentJob.status !== 'awaiting_input') {
        await onComplete();
        return;
      }
      const latest = await api.getTrip(trip.id);
      const patch: { expected_revision: number; [key: string]: unknown } = {
        expected_revision: latest.revision,
      };
      if (budget !== initialBudget && parseBudget(budget) !== latest.budget_cents) {
        patch.budget_cents = parseBudget(budget);
      }
      if (missing.includes('start_date')) {
        if (!startDate) throw new Error('请填写开始日期');
        if (latest.start_date !== startDate) patch.start_date = startDate;
      }
      if (missing.includes('end_date')) {
        if (!endDate) throw new Error('请填写结束日期');
        if (latest.end_date !== endDate) patch.end_date = endDate;
      }
      if (missing.includes('travelers')) {
        const count = parseTravelers(travelers);
        if (count === null) throw new Error('请填写同行人数');
        if (latest.travelers !== count) patch.travelers = count;
      }
      const normalized = text.trim();
      const turns = latest.clarifications ?? [];
      const alreadySaved = turns.some((turn) => turn.job_id === job.id &&
        turn.request_revision === job.request_revision && turn.answer === normalized);
      if (normalized && !alreadySaved) {
        // 问答上下文单独保存，行程说明中只追加用户回答。
        patch.known_conditions = [...(latest.known_conditions ?? []),
          ...normalized.split('\n').flatMap((line) => line.trim().match(/[\s\S]{1,500}/g) ?? [])];
        patch.clarifications = [...turns, {
          job_id: job.id, request_revision: job.request_revision,
          questions: job.questions, answer: normalized,
        }];
      }
      // 保存成功、继续失败时，重试只恢复任务，不重复追加回答。
      if (Object.keys(patch).length === 1 && latest.revision <= currentJob.request_revision) {
        throw new Error('请补充或修改行程条件');
      }
      const updated = Object.keys(patch).length > 1 ? await api.patchTrip(trip.id, patch) : latest;
      await api.resumeJob(job.id, updated.revision);
      await onComplete();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      busy.current = false;
      setSaving(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      className="clarification-dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy.current) onClose();
      }}
    >
      <form onSubmit={(event) => { event.preventDefault(); void submit(answer); }}>
        <h2 id={titleId}>补充说明</h2>
        {!missing.length && <div className="clarification-questions">
          {job.questions.map((question, index) => <p key={index}>{question}</p>)}
        </div>}
        {missing.includes('start_date') && (
          <Input label="开始日期" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} disabled={saving} />
        )}
        {missing.includes('end_date') && (
          <Input label="结束日期" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} disabled={saving} />
        )}
        {missing.includes('travelers') && (
          <Input label="同行人数" inputMode="numeric" value={travelers} onChange={(event) => setTravelers(event.target.value)} disabled={saving} />
        )}
        <Textarea label="你的补充" value={answer} onChange={(event) => setAnswer(event.target.value)} disabled={saving} maxLength={2000} rows={4} />
        {budgetQuestion && (
          <Input label="门票、餐饮及当地交通预算（元，选填）" inputMode="decimal" value={budget} onChange={(event) => setBudget(event.target.value)} disabled={saving} />
        )}
        {error && <div className="trip-detail-error" role="alert">{error}</div>}
        <div className="clarification-actions">
          <Button type="button" variant="secondary" onClick={onClose} disabled={saving}>稍后补充</Button>
          {!missing.length && !budgetQuestion && !answer.trim() && (
            <Button type="button" variant="secondary" disabled={saving} onClick={() => {
              const recommendation = '未明确的偏好按推荐安排，已明确的限制保持不变。';
              setAnswer(recommendation);
              void submit(recommendation);
            }}>按推荐安排</Button>
          )}
          <Button type="submit" loading={saving}>保存并继续</Button>
        </div>
      </form>
    </dialog>
  );
}
