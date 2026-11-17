import { useState, useRef } from 'react';
import { X } from 'lucide-react';
import { api } from '../api';
import type { Trip, Place, Activity, ActivityCost } from '../types';
import { Button } from './Button';
import { Card } from './Card';
import { Input, Select } from './Input';
import { parseMinute, formatMinute } from '../utils';
import { activityEndMinute, editableCosts, endDayOf } from '../utils/activity';
import { formatApiError } from '../utils/errors';
import './ActivityEditor.css';

export type ActivitySubmission = {
  activity: Omit<Activity, 'id'> & { place_query?: string };
  before_activity_id?: string | null;
  op?: 'replace';
  activity_id?: string;
};

interface ActivityEditorProps {
  trip: Trip;
  beforeActivityId?: string;
  editingActivity?: Activity & { place_name: string };
  onSuccess: (data: ActivitySubmission) => Promise<void>;
  onCancel: () => void;
  disabled?: boolean;
}

const BASIS_OPTIONS = (
  <>
    <option value="per_person">每人</option>
    <option value="per_group">每组</option>
    <option value="fixed">固定</option>
  </>
);

function priceText(cost: ActivityCost | undefined): string {
  return cost?.amount_cents != null ? String(cost.amount_cents / 100) : '';
}

export function ActivityEditor({
  trip,
  beforeActivityId,
  editingActivity: original,
  onSuccess,
  onCancel,
  disabled = false,
}: ActivityEditorProps) {
  const ticket = original?.costs.find((cost) => cost.category === 'ticket');
  const meal = original?.costs.find((cost) => cost.category === 'meal');

  const [query, setQuery] = useState(original?.place_name || '');
  const [places, setPlaces] = useState<Place[]>([]);
  const [selected, setSelected] = useState<Place | null>(null);
  const [searching, setSearching] = useState(false);
  const [kind, setKind] = useState<Activity['kind']>(original?.kind || 'sightseeing');
  const [period, setPeriod] = useState<Activity['period']>(original?.period || 'morning');
  const [start, setStart] = useState(formatMinute(original?.start_minute ?? 540));
  const [end, setEnd] = useState(formatMinute(original?.end_minute ?? 720));
  const [endDay, setEndDay] = useState(endDayOf(original?.end_minute ?? 720));
  const [mode, setMode] = useState<Activity['transport_mode']>(
    original?.transport_mode || 'walking'
  );
  const [ticketPrice, setTicketPrice] = useState(priceText(ticket));
  const [mealPrice, setMealPrice] = useState(priceText(meal));
  const [ticketBasis, setTicketBasis] = useState<ActivityCost['basis']>(
    ticket?.basis || 'per_person'
  );
  const [mealBasis, setMealBasis] = useState<ActivityCost['basis']>(meal?.basis || 'per_person');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const searchVersion = useRef(0);
  const submitting = useRef(false);
  const locked = disabled || saving;

  const keepingOriginalPlace = !!original && !selected && query.trim() === original.place_name;
  const placeId = selected?.id || (keepingOriginalPlace ? original?.place_id : undefined);

  // 改动输入即作废已选地点与在途搜索结果，避免把旧地点或旧费用带到新选择上。
  function changeQuery(value: string) {
    searchVersion.current += 1;
    setQuery(value);
    setSelected(null);
    setPlaces([]);
    setSearching(false);
    setError('');
  }

  async function search(event: React.FormEvent) {
    event.preventDefault();
    if (!query.trim() || locked) return;
    const version = (searchVersion.current += 1);
    setSearching(true);
    setError('');
    setPlaces([]);
    try {
      const results = await api.lookupPlaces(trip.id, query.trim());
      if (version !== searchVersion.current) return;
      setPlaces(results);
      if (!results.length) setError('未找到地点');
    } catch (err) {
      if (version === searchVersion.current) setError(formatApiError(err));
    } finally {
      if (version === searchVersion.current) setSearching(false);
    }
  }

  function selectPlace(place: Place) {
    if (place.id !== placeId) {
      setTicketPrice('');
      setMealPrice('');
      setTicketBasis('per_person');
      setMealBasis('per_person');
    }
    setSelected(place);
    setPlaces([]);
    setError('');
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (submitting.current || disabled) return;
    if (!placeId) {
      setError('请搜索并选择地点');
      return;
    }
    const startMinute = parseMinute(start);
    const rawEnd = parseMinute(end);
    if (startMinute === null || rawEnd === null) {
      setError('请填写有效时间');
      return;
    }
    const finish = activityEndMinute(startMinute, rawEnd, endDay);
    if (finish === null) {
      setError('结束时间需要晚于开始，且不超过后日零点');
      return;
    }
    const samePlace = placeId === original?.place_id;

    let costs: ActivityCost[];
    try {
      costs = editableCosts(original?.costs || [], samePlace, [
        { category: 'ticket', value: ticketPrice, basis: ticketBasis },
        { category: 'meal', value: mealPrice, basis: mealBasis },
      ]);
    } catch (err) {
      setError(formatApiError(err));
      return;
    }

    submitting.current = true;
    setSaving(true);
    setError('');
    try {
      const activity = {
        place_id: placeId,
        ...(selected ? { place_query: query.trim() } : {}),
        kind,
        period,
        start_minute: startMinute,
        end_minute: finish,
        transport_mode: mode,
        costs,
        description: samePlace && kind === original?.kind ? original?.description ?? '' : '',
        flexible_meal: kind === 'meal' ? original?.flexible_meal ?? true : false,
      };
      await onSuccess(
        original
          ? { op: 'replace', activity_id: original.id, activity }
          : { activity, before_activity_id: beforeActivityId || null }
      );
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      submitting.current = false;
      setSaving(false);
    }
  }

  return (
    <Card>
      <div className="activity-editor">
        <div className="activity-editor-header">
          <h3 className="activity-editor-title">{original ? '编辑活动' : '添加活动'}</h3>
          <Button variant="secondary" size="sm" onClick={onCancel} disabled={saving} aria-label="关闭">
            <X size={16} />
          </Button>
        </div>
        {error && (
          <div role="alert" className="activity-editor-error">
            {error}
          </div>
        )}
        <form onSubmit={search} className="activity-editor-search" role="search">
          <Input
            aria-label="地点搜索"
            value={query}
            disabled={locked}
            onChange={(e) => changeQuery(e.target.value)}
          />
          <Button
            type="submit"
            loading={searching}
            disabled={!query.trim() || locked}
          >
            搜索
          </Button>
        </form>
        {places.length > 0 && (
          <div className="place-list" role="listbox" aria-label="搜索结果">
            {places.map((place) => (
              <button
                key={place.id}
                type="button"
                role="option"
                aria-selected={selected?.id === place.id}
                className={`place-item ${selected?.id === place.id ? 'selected' : ''}`}
                onClick={() => selectPlace(place)}
              >
                <span className="place-name">{place.name}</span>
                {place.address && <span className="place-address">{place.address}</span>}
              </button>
            ))}
          </div>
        )}
        {(selected || original) && (
          <form onSubmit={save} className="activity-editor-form" noValidate>
            <fieldset disabled={locked} className="editor-fields">
              <div className="form-grid">
                <Select label="类型" value={kind} onChange={(e) => setKind(e.target.value as Activity['kind'])}>
                  <option value="sightseeing">游览</option>
                  <option value="meal">用餐</option>
                  <option value="leisure">休闲</option>
                </Select>
                <Select label="时段" value={period} onChange={(e) => setPeriod(e.target.value as Activity['period'])}>
                  <option value="morning">上午</option>
                  <option value="noon">中午</option>
                  <option value="evening">傍晚</option>
                </Select>
                <Input
                  label="开始时间"
                  type="time"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
                <Input
                  label="结束时间"
                  type="time"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
                <Select label="结束日期" value={endDay} onChange={(e) => setEndDay(Number(e.target.value))}>
                  <option value={0}>当天</option>
                  <option value={1}>次日</option>
                  <option value={2}>后日零点</option>
                </Select>
                <Select
                  label="交通方式"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as Activity['transport_mode'])}
                >
                  <option value="walking">步行</option>
                  <option value="transit">公交</option>
                  <option value="driving">驾车</option>
                </Select>
                <Input
                  label="门票（元）"
                  inputMode="decimal"
                  value={ticketPrice}
                  onChange={(e) => setTicketPrice(e.target.value)}
                />
                <Select
                  label="门票计费方式"
                  value={ticketBasis}
                  onChange={(e) => setTicketBasis(e.target.value as ActivityCost['basis'])}
                >
                  {BASIS_OPTIONS}
                </Select>
                <Input
                  label="餐饮（元）"
                  inputMode="decimal"
                  value={mealPrice}
                  onChange={(e) => setMealPrice(e.target.value)}
                />
                <Select
                  label="餐饮计费方式"
                  value={mealBasis}
                  onChange={(e) => setMealBasis(e.target.value as ActivityCost['basis'])}
                >
                  {BASIS_OPTIONS}
                </Select>
              </div>
            </fieldset>
            <div className="form-actions">
              <Button variant="secondary" type="button" onClick={onCancel} disabled={saving}>
                取消
              </Button>
              <Button
                type="submit"
                loading={saving}
                disabled={!placeId || searching || disabled}
              >
                {original ? '保存' : '添加'}
              </Button>
            </div>
          </form>
        )}
      </div>
    </Card>
  );
}
