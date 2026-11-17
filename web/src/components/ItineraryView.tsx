import { useEffect, useRef, useState } from 'react';
import {
  Trash2,
  Navigation,
  ArrowUp,
  ArrowDown,
  ExternalLink,
  Plus,
  Edit2,
} from 'lucide-react';
import type { ActivityCost, FactEvidence, Itinerary, Place, SearchSource, Trip } from '../types';
import { Button } from './Button';
import { Card } from './Card';
import { Badge } from './Badge';
import { api } from '../api';
import { generateIdempotencyKey } from '../utils';
import { formatApiError } from '../utils/errors';
import { safeExternalUrl, urlHost } from '../utils/url';
import { ActivityEditor, type ActivitySubmission } from './ActivityEditor';
import {
  formatDate,
  formatMinute,
  formatDateTime,
  formatCurrencyWhole,
  formatDuration,
  formatDistance,
  ACTIVITY_KIND_LABELS,
  PERIOD_LABELS,
  TRANSPORT_LABELS,
  COST_CATEGORY_LABELS,
  COST_BASIS_LABELS,
} from '../utils';
import './ItineraryView.css';

interface ItineraryViewProps {
  itinerary: Itinerary;
  trip: Trip;
  onUpdate: () => Promise<void> | void;
  canEdit?: boolean;
}

const FACT_KIND_LABELS: Record<FactEvidence['kind'], string> = {
  opening_hours: '营业时间',
  ticket_price: '门票',
  meal_price: '餐饮',
  reservation: '预约',
};

const FACT_STATUS_LABELS: Record<FactEvidence['status'], string> = {
  supported: '已核实',
  estimated: '估算',
  conflict: '存在冲突',
  unknown: '待核对',
};

const FACT_STATUS_VARIANTS: Record<
  FactEvidence['status'],
  'success' | 'info' | 'warning' | 'danger'
> = {
  supported: 'success',
  estimated: 'info',
  conflict: 'danger',
  unknown: 'warning',
};

function endText(minute: number): string {
  if (minute >= 2880) return '后日 00:00';
  return formatMinute(minute, true);
}

function costText(cost: ActivityCost, evidence: FactEvidence | undefined): string {
  const amount =
    cost.amount_cents != null ? formatCurrencyWhole(cost.amount_cents) : '待确认';
  const basis = COST_BASIS_LABELS[cost.basis];
  let suffix = '';
  if (evidence?.status === 'conflict') suffix = '（存在冲突）';
  else if (evidence?.status === 'unknown') suffix = '（待核对）';
  else if (evidence?.status === 'estimated' || (!evidence && cost.amount_cents != null)) {
    suffix = '（估算）';
  }
  return `${COST_CATEGORY_LABELS[cost.category]} ${amount} · ${basis}${suffix}`;
}

function factValue(fact: FactEvidence): string {
  if (fact.kind === 'reservation') return fact.reservation_note || '预约规则待确认';
  if (fact.kind === 'opening_hours') {
    const parts: string[] = [];
    if (fact.closed) parts.push('当日闭馆');
    if (fact.windows?.length) {
      parts.push(
        fact.windows
          .map((w) => `${formatMinute(w.start_minute)}–${endText(w.end_minute)}`)
          .join('、')
      );
    }
    if (fact.last_entry_minute != null) {
      parts.push(`最后入场 ${formatMinute(fact.last_entry_minute, fact.last_entry_minute >= 1440)}`);
    }
    return parts.length ? parts.join('；') : '未取得明确时间';
  }
  if (fact.amount_cents != null) {
    return `${formatCurrencyWhole(fact.amount_cents)} · ${COST_BASIS_LABELS[fact.basis]}`;
  }
  return '金额未知';
}

function sourceName(source: SearchSource | undefined, urls: (string | null | undefined)[]): string {
  if (source?.title) return source.title;
  for (const url of urls) {
    const host = urlHost(url);
    if (host) return host;
  }
  return '来源';
}

function ActivityFacts({
  facts,
  sources,
}: {
  facts: FactEvidence[];
  sources: Map<string, SearchSource>;
}) {
  const [open, setOpen] = useState(false);
  if (!facts.length) return null;
  return (
    <div className="activity-facts">
      <button
        type="button"
        className="disclosure-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        事实依据（{facts.length}）
      </button>
      {open && (
        <ul className="fact-list">
          {facts.map((fact) => {
            const source = sources.get(fact.source_id);
            const href = safeExternalUrl(fact.source_url) ?? safeExternalUrl(source?.url);
            const name = sourceName(source, [fact.source_url, source?.url]);
            return (
              <li key={fact.id} className="fact-item">
                <div className="fact-head">
                  <span className="fact-title">{FACT_KIND_LABELS[fact.kind]}</span>
                  <Badge variant={FACT_STATUS_VARIANTS[fact.status]} showDot={false}>
                    {FACT_STATUS_LABELS[fact.status]}
                  </Badge>
                  <span className="fact-value">{factValue(fact)}</span>
                </div>
                {fact.quote && <blockquote className="fact-quote">{fact.quote}</blockquote>}
                {fact.applicability?.quote && fact.applicability.quote !== fact.quote && (
                  <blockquote className="fact-quote">{fact.applicability.quote}</blockquote>
                )}
                <div className="fact-meta">
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`打开来源：${name}`}
                    >
                      <ExternalLink size={12} />
                      {name}
                    </a>
                  ) : (
                    <span className="fact-source">{name}</span>
                  )}
                  <span>查询于 {formatDateTime(fact.queried_at)}</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function ItineraryView({ itinerary, trip, onUpdate, canEdit = true }: ItineraryViewProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [showAddEditor, setShowAddEditor] = useState<{ date: string } | null>(null);
  const [editingActivity, setEditingActivity] = useState<string | null>(null);
  const editBase = useRef(itinerary.revision);

  // 草案版本变化、切到历史版或不可编辑时收起表单，避免留下能提交旧版本的操作。
  useEffect(() => {
    editBase.current = itinerary.revision;
    setShowAddEditor(null);
    setEditingActivity(null);
    setSubmitting(false);
  }, [itinerary.revision, canEdit]);

  const placesMap = new Map<string, Place>(itinerary.places.map((place) => [place.id, place]));
  const transportMap = new Map(itinerary.transport.map((leg) => [leg.from_activity_id, leg]));
  const evidenceMap = new Map(itinerary.evidence.map((fact) => [fact.id, fact]));
  const sourcesMap = new Map(itinerary.sources.map((source) => [source.id, source]));
  const validation = itinerary.validation;

  async function submit(operations: unknown[]) {
    // 提交前再核对一次，布局已更新或进入只读时不发出旧版本编辑。
    if (!canEdit || editBase.current !== itinerary.revision) {
      throw new Error('行程已更新，请重新编辑');
    }
    await api.editItinerary(
      trip.id,
      itinerary.revision,
      itinerary.request_revision,
      generateIdempotencyKey(),
      operations
    );
    await onUpdate();
  }

  async function handleEdit(operation: unknown) {
    if (!canEdit) return;
    setSubmitting(true);
    setError('');
    try {
      await submit([operation]);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  // 编辑回调的错误交给表单展示，成功后先刷新草案再收起表单。
  async function handleAddActivity(data: ActivitySubmission, date: string) {
    setSubmitting(true);
    try {
      await submit([{ op: 'add', on_date: date, ...data }]);
      setShowAddEditor(null);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReplaceActivity(data: ActivitySubmission) {
    setSubmitting(true);
    try {
      await submit([data]);
      setEditingActivity(null);
    } finally {
      setSubmitting(false);
    }
  }
  function handleRemove(activityId: string) {
    if (!confirm('确定删除该活动？')) return;
    handleEdit({ op: 'remove', activity_id: activityId });
  }

  function handleMove(activityId: string, targetDate: string, beforeActivityId?: string) {
    handleEdit({
      op: 'move',
      activity_id: activityId,
      target_date: targetDate,
      before_activity_id: beforeActivityId || null,
    });
  }

  function handleMoveToDay(activityId: string, targetDate: string) {
    if (!confirm(`确定将此活动移至 ${formatDate(targetDate)}？`)) return;
    handleMove(activityId, targetDate);
  }

  return (
    <div className="itinerary-view">
      <Card>
        <div className="itinerary-summary">
          <div className="itinerary-header">
            <h2 className="itinerary-title">行程草案</h2>
          </div>

          {error && (
            <div className="itinerary-error" role="alert">
              {error}
            </div>
          )}

          <details className="itinerary-details">
            <summary>费用与依据</summary>
            <div className="itinerary-budget">
              <span>已知费用 {formatCurrencyWhole(validation.known_cost_cents)}</span>
              {validation.estimated_cost_cents > 0 && <span>估算费用 {formatCurrencyWhole(validation.estimated_cost_cents)}</span>}
              {validation.unknown_cost_count > 0 && <span>待确认费用 {validation.unknown_cost_count}项</span>}
            </div>
            {validation.issues.length > 0 && <details>
              <summary>核验详情</summary>
              {validation.issues.map((issue, index) => <p key={index}>{issue.message}</p>)}
            </details>}
            {itinerary.assumptions.length > 0 && <details>
              <summary>安排说明</summary>
              {itinerary.assumptions.map((text, index) => <p key={index}>{text}</p>)}
            </details>}
          </details>
        </div>
      </Card>

      {itinerary.days.map((day) => {
        const activities = day.activities;
        const isAddingToThisDay = showAddEditor?.date === day.on_date;

        return (
          <Card key={day.on_date}>
            <div className="day-plan">
              <div className="day-plan-header">
                <h3 className="day-plan-title">{formatDate(day.on_date)}</h3>
                {canEdit && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setShowAddEditor({ date: day.on_date })}
                    disabled={submitting || isAddingToThisDay}
                  >
                    <Plus size={14} />
                    添加活动
                  </Button>
                )}
              </div>

              {activities.length === 0 && <div className="day-empty">当天没有活动</div>}

              {activities.map((activity, activityIndex) => {
                const place = placesMap.get(activity.place_id);
                const transport = transportMap.get(activity.id);
                const facts = itinerary.evidence.filter(
                  (fact) => fact.place_id === activity.place_id && fact.on_date === day.on_date
                );
                const isEditing = editingActivity === activity.id;

                if (isEditing) {
                  return (
                    <ActivityEditor
                      key={activity.id}
                      trip={trip}
                      editingActivity={{
                        id: activity.id,
                        place_id: activity.place_id,
                        place_name: place?.name || '',
                        kind: activity.kind,
                        period: activity.period,
                        start_minute: activity.start_minute,
                        end_minute: activity.end_minute,
                        transport_mode: activity.transport_mode,
                        costs: activity.costs,
                        description: activity.description,
                        flexible_meal: activity.flexible_meal,
                      }}
                      onSuccess={handleReplaceActivity}
                      onCancel={() => setEditingActivity(null)}
                      disabled={!canEdit || submitting}
                    />
                  );
                }

                return (
                  <div key={activity.id} className="activity-block">
                    <div className="activity-card">
                      <div className="activity-header">
                        <div className="activity-main">
                          <div className="activity-place">{activity.kind === 'meal'
                            ? (activity.start_minute < 600 ? '早餐' : activity.start_minute < 960 ? '午餐' : '晚餐')
                            : place?.name || '未知地点'}</div>
                          <div className="activity-time">
                            <Badge variant="info" showDot={false}>
                              {PERIOD_LABELS[activity.period]}
                            </Badge>
                            <span>
                              {activity.kind === 'meal' && '约 '}
                              {formatMinute(activity.kind === 'meal'
                                ? Math.floor(activity.start_minute / 30) * 30 : activity.start_minute)}–{endText(activity.kind === 'meal'
                                ? Math.ceil(activity.end_minute / 30) * 30 : activity.end_minute)}
                            </span>
                            <Badge variant="default" showDot={false}>
                              {ACTIVITY_KIND_LABELS[activity.kind]}
                            </Badge>
                          </div>
                          {activity.description && <p className="activity-description">{activity.description}</p>}
                          {activity.kind !== 'meal' && place?.address && <div className="activity-address">{place.address}</div>}
                          {activity.costs.length > 0 && (
                            <div className="activity-costs">
                              {activity.costs.map((cost, i) => (
                                <span key={i} className="activity-cost">
                                  {costText(
                                    cost,
                                    cost.evidence_id ? evidenceMap.get(cost.evidence_id) : undefined
                                  )}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                        {canEdit && (
                          <div className="activity-actions">
                            {activityIndex > 0 && (
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() =>
                                  handleMove(
                                    activity.id,
                                    day.on_date,
                                    activities[activityIndex - 1].id
                                  )
                                }
                                disabled={submitting}
                                aria-label="上移活动"
                              >
                                <ArrowUp size={14} />
                              </Button>
                            )}
                            {activityIndex < activities.length - 1 && (
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() =>
                                  handleMove(
                                    activity.id,
                                    day.on_date,
                                    activityIndex + 2 < activities.length
                                      ? activities[activityIndex + 2].id
                                      : undefined
                                  )
                                }
                                disabled={submitting}
                                aria-label="下移活动"
                              >
                                <ArrowDown size={14} />
                              </Button>
                            )}
                            {itinerary.days.length > 1 && (
                              <select
                                className="activity-move-select"
                                onChange={(e) => {
                                  if (e.target.value && e.target.value !== day.on_date) {
                                    handleMoveToDay(activity.id, e.target.value);
                                    e.target.value = '';
                                  }
                                }}
                                disabled={submitting}
                                defaultValue=""
                                aria-label="移至其他日期"
                              >
                                <option value="">移至...</option>
                                {itinerary.days
                                  .filter((item) => item.on_date !== day.on_date)
                                  .map((item) => (
                                    <option key={item.on_date} value={item.on_date}>
                                      {formatDate(item.on_date)}
                                    </option>
                                  ))}
                              </select>
                            )}
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => setEditingActivity(activity.id)}
                              disabled={submitting}
                              aria-label="编辑活动"
                            >
                              <Edit2 size={14} />
                            </Button>
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => handleRemove(activity.id)}
                              disabled={submitting}
                              aria-label="删除活动"
                            >
                              <Trash2 size={14} />
                            </Button>
                          </div>
                        )}
                      </div>
                      {validation.issues.filter((issue) => issue.severity === 'conflict' &&
                        issue.object_ids[0] === activity.id).slice(0, 1).map((issue, index) => (
                        <div className="activity-warning" key={index}>
                          {['closed', 'opening_hours', 'last_entry'].includes(issue.rule)
                            ? '开放时间待调整' : ['travel_time', 'quality_geography'].includes(issue.rule)
                              ? '接驳安排待调整' : '安排待调整'}
                        </div>
                      ))}
                      <ActivityFacts facts={activity.kind === 'meal' && activity.flexible_meal ? [] : facts} sources={sourcesMap} />
                    </div>
                    {transport && (
                      <div className="transport-info">
                        <Navigation size={14} />
                        <span>
                          {TRANSPORT_LABELS[transport.route.mode]}
                          {transport.route.duration_seconds != null && (
                            <> · {formatDuration(Math.round(transport.route.duration_seconds / 60))}</>
                          )}
                          {transport.route.distance_meters != null && (
                            <> · {formatDistance(transport.route.distance_meters)}</>
                          )}
                          {transport.route.fare_cents != null && (
                            <> · {formatCurrencyWhole(transport.route.fare_cents)}</>
                          )}
                          {transport.route.status === 'estimated' && '（估算）'}
                          {transport.route.status === 'unknown' && '（待确认）'}
                        </span>
                      </div>
                    )}
                  </div>
                );
              })}

              {isAddingToThisDay && (
                <ActivityEditor
                  trip={trip}
                  onSuccess={(data) => handleAddActivity(data, day.on_date)}
                  onCancel={() => setShowAddEditor(null)}
                  disabled={!canEdit || submitting}
                />
              )}
            </div>
          </Card>
        );
      })}
    </div>
  );
}
