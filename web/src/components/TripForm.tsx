import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { api } from '../api';
import type { Trip, TripFields, TimeConstraint } from '../types';
import { Button } from './Button';
import { Input, Select, Textarea } from './Input';
import { Card } from './Card';
import { formatApiError } from '../utils/errors';
import {
  CONSTRAINT_KIND_LABELS,
  buildTimeConstraints,
  constraintToDraft,
  emptyConstraint,
  parseBudget,
  parseTravelers,
  type ConstraintDraft,
} from '../utils/trip';
import './TripForm.css';

interface TripFormProps {
  trip?: Trip;
  onSuccess: (trip: Trip) => void;
  onCancel: () => void;
}

function splitLines(value: string): string[] {
  return value.split('\n').map((s) => s.trim()).filter(Boolean);
}

export function TripForm({ trip, onSuccess, onCancel }: TripFormProps) {
  const [destination, setDestination] = useState(trip?.destination || '');
  const [startDate, setStartDate] = useState(trip?.start_date || '');
  const [endDate, setEndDate] = useState(trip?.end_date || '');
  const [travelers, setTravelers] = useState(trip?.travelers?.toString() || '');
  const [budget, setBudget] = useState(
    trip?.budget_cents != null ? (trip.budget_cents / 100).toString() : ''
  );
  const [requirements, setRequirements] = useState((trip?.requirements || []).join('\n'));
  const [priorities, setPriorities] = useState((trip?.priorities || []).join('\n'));
  const [knownConditions, setKnownConditions] = useState(
    (trip?.known_conditions || []).join('\n')
  );
  const [constraints, setConstraints] = useState<ConstraintDraft[]>(() =>
    (trip?.time_constraints || []).map(constraintToDraft)
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  function updateConstraint(index: number, patch: Partial<ConstraintDraft>) {
    setConstraints((items) =>
      items.map((item, i) => (i === index ? { ...item, ...patch } : item))
    );
  }

  function removeConstraint(index: number) {
    setConstraints((items) => items.filter((_, i) => i !== index));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');

    let travelersValue: number | null;
    let budgetCents: number | null;
    let timeConstraints: TimeConstraint[];
    try {
      travelersValue = parseTravelers(travelers);
      budgetCents = parseBudget(budget);
      timeConstraints = buildTimeConstraints(constraints, {
        start_date: startDate || null,
        end_date: endDate || null,
      });
    } catch (err) {
      setError(formatApiError(err));
      return;
    }

    const fields: TripFields = {
      destination: destination.trim(),
      start_date: startDate || null,
      end_date: endDate || null,
      travelers: travelersValue,
      budget_cents: budgetCents,
      requirements: splitLines(requirements),
      priorities: splitLines(priorities),
      known_conditions: splitLines(knownConditions),
      time_constraints: timeConstraints,
    };

    try {
      setLoading(true);
      const result = trip
        ? await api.patchTrip(trip.id, {
            expected_revision: trip.revision,
            ...fields,
          })
        : await api.createTrip(fields);
      onSuccess(result);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <form onSubmit={handleSubmit} className="trip-form" noValidate>
        {error && (
          <div className="trip-form-error" role="alert">
            {error}
          </div>
        )}

        <Input
          label="目的地"
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          placeholder="西安"
          required
        />

        <div className="trip-form-row">
          <Input
            label="开始日期"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
          <Input
            label="结束日期"
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </div>

        <div className="trip-form-row">
          <Input
            label="同行人数"
            inputMode="numeric"
            value={travelers}
            onChange={(e) => setTravelers(e.target.value)}
            placeholder="2"
          />
          <Input
            label="全程预算（元）"
            inputMode="decimal"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            placeholder="3000"
          />
        </div>

        <Textarea
          label="要求"
          value={requirements}
          onChange={(e) => setRequirements(e.target.value)}
          placeholder="素食"
        />

        <Textarea
          label="重点"
          value={priorities}
          onChange={(e) => setPriorities(e.target.value)}
          placeholder="博物馆"
        />

        <Textarea
          label="已知条件"
          value={knownConditions}
          onChange={(e) => setKnownConditions(e.target.value)}
          placeholder="住在钟楼附近"
        />

        <div className="trip-form-section">
          <div className="trip-form-section-header">
            <span className="input-label">时间安排</span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setConstraints((items) => [...items, emptyConstraint()])}
            >
              <Plus size={14} />
              添加
            </Button>
          </div>
          {constraints.map((item, index) => (
            <div
              key={index}
              className="constraint-row"
              role="group"
              aria-label={`时间安排 ${index + 1}`}
            >
              <Select
                label="类型"
                value={item.kind}
                onChange={(e) =>
                  updateConstraint(index, { kind: e.target.value as ConstraintDraft['kind'] })
                }
              >
                {Object.entries(CONSTRAINT_KIND_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
              <Input
                label="日期"
                type="date"
                value={item.on_date}
                min={startDate || undefined}
                max={endDate || undefined}
                onChange={(e) => updateConstraint(index, { on_date: e.target.value })}
              />
              <Input
                label="开始时间"
                type="time"
                value={item.start}
                onChange={(e) => updateConstraint(index, { start: e.target.value })}
              />
              <Input
                label="结束时间"
                type="time"
                value={item.end}
                onChange={(e) => updateConstraint(index, { end: e.target.value })}
              />
              {item.end && (
                <Select
                  label="结束日期"
                  value={item.endDay}
                  onChange={(e) => updateConstraint(index, { endDay: Number(e.target.value) })}
                >
                  <option value={0}>当天</option>
                  <option value={1}>次日</option>
                  <option value={2}>后日零点</option>
                </Select>
              )}
              <Input
                label="地点"
                value={item.place}
                onChange={(e) => updateConstraint(index, { place: e.target.value })}
                placeholder={item.kind === 'reservation' ? '具体场馆' : '可选'}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => removeConstraint(index)}
                aria-label={`删除时间安排 ${index + 1}`}
              >
                <Trash2 size={14} />
              </Button>
            </div>
          ))}
        </div>

        <div className="trip-form-actions">
          <Button type="button" variant="secondary" onClick={onCancel}>
            取消
          </Button>
          <Button type="submit" loading={loading}>
            保存
          </Button>
        </div>
      </form>
    </Card>
  );
}
