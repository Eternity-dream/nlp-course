import { useEffect, useState } from 'react';
import { api } from '../api';
import type { PreferenceProfile } from '../types';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Textarea } from '../components/Input';
import { Loading } from '../components/Loading';
import { formatApiError } from '../utils/errors';
import './PreferencesPage.css';

export function PreferencesPage() {
  const [profile, setProfile] = useState<PreferenceProfile | null>(null);
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const data = await api.getPreferences();
      setProfile(data);
      setNotes(data.notes);
      setError('');
    } catch (err) { setError(formatApiError(err)); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);

  async function save(value: string) {
    if (!profile || saving) return;
    setSaving(true);
    setSaved(false);
    setError('');
    try {
      const data = await api.putPreferences(profile.revision, value);
      setProfile(data);
      setNotes(data.notes);
      setSaved(true);
    } catch (err) { setError(formatApiError(err)); }
    finally { setSaving(false); }
  }

  if (loading) return <Loading text="加载中" />;
  return <div className="preferences-page">
    <div className="preferences-header"><h1 className="preferences-title">旅行偏好</h1></div>
    {error && <div className="preferences-error" role="alert">{error}
      <Button variant="secondary" onClick={load}>重新加载</Button>
    </div>}
    {profile && <Card><form className="preferences-form" onSubmit={(event) => { event.preventDefault(); void save(notes); }}>
      <Textarea label="旅行习惯" value={notes} rows={8} maxLength={10000} disabled={saving}
        onChange={(event) => { setNotes(event.target.value); setSaved(false); }} />
      <div className="preferences-actions">
        {saved && <span role="status">已保存</span>}
        <Button type="button" variant="secondary" disabled={saving || !notes} onClick={() => {
          if (confirm('确定清空旅行偏好？')) void save('');
        }}>清空</Button>
        <Button type="submit" loading={saving}>保存</Button>
      </div>
    </form></Card>}
  </div>;
}
