import { useState, useEffect, useRef } from 'react';
import { ArrowLeft, Play } from 'lucide-react';
import { api } from '../api';
import type { Trip, PlanJob, Itinerary } from '../types';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Badge } from '../components/Badge';
import { Loading } from '../components/Loading';
import { formatApiError } from '../utils/errors';
import { navigate } from '../utils/router';
import { formatDate, formatMinute, formatCurrencyWhole, generateIdempotencyKey, JOB_STATUS_LABELS } from '../utils';
import { CONSTRAINT_KIND_LABELS } from '../utils/trip';
import { ItineraryView } from '../components/ItineraryView';
import { TripForm } from '../components/TripForm';
import './TripDetailPage.css';

const STAGE_LABELS: Record<string, string> = {
  conditions: '整理条件',
  knowledge: '检索资料',
  places: '查询地点',
  evidence: '核对来源',
  generation: '生成行程',
  repair: '调整行程',
  routes: '查询路线',
  validation: '检查行程',
  saving: '保存草稿',
  editing: '编辑中',
  reschedule: '重排活动',
  constraints: '检查条件',
};

const MISSING_FIELD_LABELS: Record<string, string> = {
  start_date: '开始日期',
  end_date: '结束日期',
  travelers: '同行人数',
};

const ACTIVE_STATUSES: PlanJob['status'][] = ['queued', 'running', 'awaiting_input'];

interface TripDetailPageProps {
  tripId: string;
}

export function TripDetailPage({ tripId }: TripDetailPageProps) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [jobs, setJobs] = useState<PlanJob[]>([]);
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [error, setError] = useState('');
  const [selectedRevision, setSelectedRevision] = useState<number | null>(null);
  const [latestRevision, setLatestRevision] = useState<number | null>(null);
  const [loadingRevision, setLoadingRevision] = useState(false);
  const pollTimerRef = useRef<number>();
  const mountedRef = useRef(true);
  const requestSeq = useRef(0);
  const selectionRef = useRef<number | null>(null);
  const hasActiveJobRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    loadTrip();
    return () => {
      mountedRef.current = false;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, [tripId]);

  // 草案读取带上请求序号与当前选择，慢响应不会覆盖之后的选择或轮询结果。
  async function readItinerary(revision: number | null): Promise<void> {
    const seq = ++requestSeq.current;
    const target = selectionRef.current;
    setLoadingRevision(true);
    try {
      const data = await api.getItinerary(tripId, revision ?? undefined);
      if (!mountedRef.current || seq !== requestSeq.current || selectionRef.current !== target) return;
      setItinerary(data);
    } finally {
      if (mountedRef.current && seq === requestSeq.current) setLoadingRevision(false);
    }
  }

  async function loadTrip(isBackground = false) {
    if (!isBackground) setLoading(true);
    try {
      const [tripData, jobsData] = await Promise.all([api.getTrip(tripId), api.listJobs(tripId)]);
      if (!mountedRef.current) return;
      setTrip(tripData);
      setJobs(jobsData);
      setError('');
      hasActiveJobRef.current = jobsData.some((job) => ACTIVE_STATUSES.includes(job.status));

      const latestSucceeded = jobsData.find(
        (job) => job.status === 'succeeded' && job.result_revision != null
      );
      const latest = latestSucceeded?.result_revision ?? null;
      setLatestRevision(latest);
      if (latest === null) {
        // 还没有草案时只做任务轮询，不能中断后续状态更新。
        setItinerary(null);
      } else {
        const target = selectionRef.current;
        const showLatest = target === null || target === latest;
        if (!showLatest) {
          setSelectedRevision(target);
        } else if (target !== null) {
          selectionRef.current = null;
          setSelectedRevision(null);
        }
        await readItinerary(showLatest ? null : target);
      }
    } catch (err) {
      if (mountedRef.current) setError(formatApiError(err));
    } finally {
      if (mountedRef.current) {
        setLoading(false);
        // 网络短暂失败后仍继续轮询，直到任务结束。
        if (hasActiveJobRef.current) schedulePoll();
      }
    }
  }

  function schedulePoll() {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    pollTimerRef.current = window.setTimeout(() => {
      if (mountedRef.current) loadTrip(true);
    }, 2000);
  }

  async function handleSubmitJob() {
    if (!trip) return;
    if (trip.missing_fields.length > 0) {
      setError('请先填写完整的目的地、日期和人数');
      setEditing(true);
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      selectionRef.current = null;
      setSelectedRevision(null);
      await api.submitJob(trip.id, trip.revision, generateIdempotencyKey());
      await loadTrip(true);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      if (mountedRef.current) setSubmitting(false);
    }
  }

  async function handleRetryJob(job: PlanJob) {
    if (!trip) return;
    setError('');
    try {
      await api.retryJob(job.id, trip.revision, generateIdempotencyKey());
      await loadTrip(true);
    } catch (err) {
      setError(formatApiError(err));
    }
  }

  async function handleResumeJob(job: PlanJob, revision: number) {
    setError('');
    try {
      await api.resumeJob(job.id, revision);
      await loadTrip(true);
    } catch (err) {
      setError(formatApiError(err));
    }
  }

  async function handleChangeRevision(rev: number) {
    const target = rev === latestRevision ? null : rev;
    const previous = selectionRef.current;
    selectionRef.current = target;
    setSelectedRevision(target);
    setError('');
    try {
      await readItinerary(target);
    } catch (err) {
      // 失败时回到原选择，避免把旧内容显示为刚选的版本。
      if (mountedRef.current && selectionRef.current === target) {
        selectionRef.current = previous;
        setSelectedRevision(previous);
        setError(formatApiError(err));
      }
    }
  }

  if (loading) return <Loading text="加载中" />;

  if (!trip) {
    return (
      <div className="trip-detail-page">
        <div className="trip-detail-header">
          <Button variant="secondary" size="sm" onClick={() => navigate('/')}>
            <ArrowLeft size={16} />
            返回列表
          </Button>
          <h1 className="trip-detail-title">加载失败</h1>
        </div>
        <Card>
          <div className="trip-detail-error" role="alert">
            {error || '请求失败'}
          </div>
          <Button onClick={() => loadTrip()} style={{ marginTop: 'var(--spacing-12)' }}>
            重试
          </Button>
        </Card>
      </div>
    );
  }

  if (editing) {
    const awaitingJob = jobs.find((job) => job.status === 'awaiting_input');
    return (
      <div className="trip-detail-page">
        <div className="trip-detail-header">
          <Button variant="secondary" size="sm" onClick={() => setEditing(false)}>
            <ArrowLeft size={16} />
            返回
          </Button>
          <h1 className="trip-detail-title">{awaitingJob ? '补充要求' : '编辑行程要求'}</h1>
        </div>
        <TripForm
          trip={trip}
          onSuccess={async (updated) => {
            setTrip(updated);
            setEditing(false);
            if (awaitingJob) {
              await handleResumeJob(awaitingJob, updated.revision);
            } else {
              await loadTrip(true);
            }
          }}
          onCancel={() => setEditing(false)}
        />
      </div>
    );
  }

  const latestJob = jobs[0];
  const activeJob = jobs.find((job) => ACTIVE_STATUSES.includes(job.status));
  const failedJob =
    latestJob && ['failed', 'interrupted'].includes(latestJob.status) ? latestJob : null;
  const isAwaitingInput = activeJob?.status === 'awaiting_input';
  const isRunning = activeJob != null && activeJob.status !== 'awaiting_input';
  const isHistoricalVersion =
    latestRevision !== null && selectedRevision !== null && selectedRevision !== latestRevision;
  const requestMismatch = itinerary != null && itinerary.request_revision !== trip.revision;
  // 背景轮询只短暂置 loadingRevision，不参与可编辑判断，避免每次刷新收起用户表单。
  // 显示的草案版本必须就是最新版本，切回“最新”的加载间隙不会把旧内容当最新版提交。
  const canEditItinerary =
    itinerary != null &&
    itinerary.revision === latestRevision &&
    !isHistoricalVersion &&
    !activeJob &&
    !requestMismatch;
  // 补充要求需要编辑表单，仅生成或编辑任务执行中禁止改动。
  const canEditTrip = !isRunning && !loadingRevision;
  const canSubmit = trip.missing_fields.length === 0 && !activeJob && !submitting;
  const missingFields = trip.missing_fields
    .map((field) => MISSING_FIELD_LABELS[field])
    .filter((label): label is string => Boolean(label));

  const versionOptions = jobs.filter(
    (job) => job.status === 'succeeded' && job.result_revision != null
  );

  const requirements = trip.requirements ?? [];
  const priorities = trip.priorities ?? [];
  const knownConditions = trip.known_conditions ?? [];
  const timeConstraints = trip.time_constraints ?? [];

  return (
    <div className="trip-detail-page">
      <div className="trip-detail-header">
        <Button variant="secondary" size="sm" onClick={() => navigate('/')}>
          <ArrowLeft size={16} />
          返回列表
        </Button>
        <h1 className="trip-detail-title">{trip.destination}</h1>
      </div>

      {error && (
        <div className="trip-detail-error" role="alert">
          {error}
        </div>
      )}

      <Card>
        <div className="trip-info">
          {trip.start_date && trip.end_date && (
            <div className="trip-info-row">
              <span className="trip-info-label">日期</span>
              <span className="trip-info-value">
                {formatDate(trip.start_date)} 至 {formatDate(trip.end_date)}
              </span>
            </div>
          )}
          {trip.travelers != null && (
            <div className="trip-info-row">
              <span className="trip-info-label">人数</span>
              <span className="trip-info-value">{trip.travelers}人</span>
            </div>
          )}
          {trip.budget_cents != null && (
            <div className="trip-info-row">
              <span className="trip-info-label">预算</span>
              <span className="trip-info-value">{formatCurrencyWhole(trip.budget_cents)}</span>
            </div>
          )}
          {requirements.length > 0 && (
            <div className="trip-info-row">
              <span className="trip-info-label">要求</span>
              <span className="trip-info-value">{requirements.join('、')}</span>
            </div>
          )}
          {priorities.length > 0 && (
            <div className="trip-info-row">
              <span className="trip-info-label">重点</span>
              <span className="trip-info-value">{priorities.join('、')}</span>
            </div>
          )}
          {knownConditions.length > 0 && (
            <div className="trip-info-row">
              <span className="trip-info-label">已知条件</span>
              <span className="trip-info-value">{knownConditions.join('、')}</span>
            </div>
          )}
          {timeConstraints.length > 0 && (
            <div className="trip-info-row">
              <span className="trip-info-label">时间安排</span>
              <span className="trip-info-value">
                {timeConstraints.map((item, index) => (
                  <span key={index} className="trip-constraint">
                    {formatDate(item.on_date)} {CONSTRAINT_KIND_LABELS[item.kind]}{' '}
                    {formatMinute(item.minute)}
                    {item.end_minute != null &&
                      `–${formatMinute(item.end_minute, item.end_minute >= 1440)}`}
                    {item.place_name ? ` ${item.place_name}` : ''}
                  </span>
                ))}
              </span>
            </div>
          )}
          {missingFields.length > 0 && (
            <div className="trip-info-row">
              <span className="trip-info-label">待补充</span>
              <span className="trip-info-value">{missingFields.join('、')}</span>
            </div>
          )}
        </div>
        <div className="trip-info-actions">
          <Button variant="secondary" onClick={() => setEditing(true)} disabled={!canEditTrip}>
            {isAwaitingInput ? '补充要求' : '编辑要求'}
          </Button>
          {!isAwaitingInput && (
            <Button onClick={handleSubmitJob} loading={submitting} disabled={!canSubmit}>
              <Play size={16} />
              {latestRevision ? '重新生成' : '生成行程'}
            </Button>
          )}
        </div>
      </Card>

      {activeJob && (
        <Card>
          <div className="job-status">
            <div className="job-status-header">
              <span className="job-status-label">
                {isAwaitingInput ? '需要补充' : '正在生成'}
              </span>
              <JobStatusBadge status={activeJob.status} />
            </div>
            {isRunning && activeJob.stage && (
              <div className="job-status-stage">
                {STAGE_LABELS[activeJob.stage] || '处理中'}
              </div>
            )}
            {isAwaitingInput && activeJob.questions.length > 0 && (
              <div className="job-questions">
                {activeJob.questions.map((question, i) => (
                  <div key={i} className="job-question">
                    {question}
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      )}

      {failedJob && (
        <Card>
          <div className="job-status">
            <div className="job-status-header">
              <span className="job-status-label">任务未完成</span>
              <JobStatusBadge status={failedJob.status} />
            </div>
            <div className="job-status-error">{failedJob.error?.message || '任务未完成'}</div>
            <div>
              <Button size="sm" onClick={() => handleRetryJob(failedJob)} disabled={!canEditTrip}>
                重试
              </Button>
            </div>
          </div>
        </Card>
      )}

      {itinerary && (versionOptions.length > 1 || requestMismatch) && (
        <Card>
          <div className="version-bar">
            {versionOptions.length > 1 && (
              <>
                <span className="version-label">版本</span>
                <select
                  value={selectedRevision ?? latestRevision ?? ''}
                  onChange={(e) => handleChangeRevision(Number(e.target.value))}
                  disabled={loadingRevision}
                  className="version-select"
                  aria-label="行程版本"
                >
                  {versionOptions.map((job) => (
                    <option key={job.result_revision!} value={job.result_revision!}>
                      第 {job.result_revision} 版
                      {job.result_revision === latestRevision ? '（最新）' : ''}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>
          {isHistoricalVersion && <div className="version-hint">正在查看历史版本，不可编辑</div>}
          {requestMismatch && !isHistoricalVersion && (
            <div className="version-hint">条件已更新，重新生成后才能编辑</div>
          )}
        </Card>
      )}

      {itinerary && <ItineraryView itinerary={itinerary} trip={trip} onUpdate={() => loadTrip(true)} canEdit={canEditItinerary} />}

      {jobs.length > 0 && (
        <Card>
          <button
            type="button"
            className="disclosure-toggle"
            aria-expanded={showHistory}
            onClick={() => setShowHistory(!showHistory)}
          >
            任务记录（{jobs.length}）
          </button>
          {showHistory && (
            <ul className="job-history-list">
              {jobs.map((job) => (
                <li key={job.id} className="job-history-item">
                  <JobStatusBadge status={job.status} />
                  <span className="job-history-stage">
                    {job.kind === 'edit' ? '编辑行程' : '生成行程'}
                    {job.stage && ACTIVE_STATUSES.includes(job.status)
                      ? ` · ${STAGE_LABELS[job.stage] || '处理中'}`
                      : ''}
                  </span>
                  <span className="job-history-time">
                    {new Date(job.created_at).toLocaleString('zh-CN')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

function JobStatusBadge({ status }: { status: PlanJob['status'] }) {
  const variantMap: Record<string, 'default' | 'success' | 'warning' | 'danger' | 'info'> = {
    queued: 'info',
    running: 'default',
    awaiting_input: 'warning',
    succeeded: 'success',
    failed: 'danger',
    interrupted: 'warning',
  };
  return <Badge variant={variantMap[status] || 'info'}>{JOB_STATUS_LABELS[status]}</Badge>;
}
