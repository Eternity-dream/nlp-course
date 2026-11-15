import { useState, useEffect } from 'react';
import { Save } from 'lucide-react';
import { api } from '../api';
import type { PreferenceProfile } from '../types';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Input } from '../components/Input';
import { Loading } from '../components/Loading';
import { formatApiError } from '../utils/errors';
import './PreferencesPage.css';

export function PreferencesPage() {
  const [profile, setProfile] = useState<PreferenceProfile | null>(null);
  const [items, setItems] = useState<Array<{ key: string; value: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    loadPreferences();
  }, []);

  async function loadPreferences() {
    try {
      setLoading(true);
      const data = await api.getPreferences();
      setProfile(data);
      setItems(Object.entries(data.preferences).map(([key, value]) => ({ key, value })));
    } catch (err: any) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    if (!profile) return;

    const preferences: Record<string, string> = {};
    items.forEach((item) => {
      const k = item.key.trim();
      const v = item.value.trim();
      if (k && v) {
        preferences[k] = v;
      }
    });

    try {
      setSaving(true);
      setError('');
      const updated = await api.putPreferences(profile.revision, preferences);
      setProfile(updated);
      setItems(Object.entries(updated.preferences).map(([key, value]) => ({ key, value })));
    } catch (err: any) {
      setError(formatApiError(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    if (!profile) return;
    if (!confirm('确定清空所有偏好？')) return;

    try {
      setSaving(true);
      setError('');
      const updated = await api.putPreferences(profile.revision, {});
      setProfile(updated);
      setItems([]);
    } catch (err: any) {
      setError(formatApiError(err));
    } finally {
      setSaving(false);
    }
  }

  function addItem() {
    setItems([...items, { key: '', value: '' }]);
  }

  function removeItem(index: number) {
    setItems(items.filter((_, i) => i !== index));
  }

  function updateItem(index: number, field: 'key' | 'value', value: string) {
    const updated = [...items];
    updated[index][field] = value;
    setItems(updated);
  }

  if (loading) {
    return <Loading text="加载中" />;
  }

  return (
    <div className="preferences-page">
      <div className="preferences-header">
        <h1 className="preferences-title">偏好</h1>
        <div className="preferences-actions">
          <Button variant="secondary" onClick={handleClear} disabled={items.length === 0}>
            清空
          </Button>
          <Button onClick={handleSave} loading={saving}>
            <Save size={16} />
            保存
          </Button>
        </div>
      </div>

      {error && (
        <div className="preferences-error" role="alert">
          {error}
        </div>
      )}

      <Card>
        <div className="preferences-form">
          {items.map((item, index) => (
            <div key={index} className="preference-item">
              <Input
                placeholder="节奏"
                aria-label={`偏好项 ${index + 1}`}
                value={item.key}
                onChange={(e) => updateItem(index, 'key', e.target.value)}
              />
              <Input
                placeholder="慢节奏"
                aria-label={`偏好内容 ${index + 1}`}
                value={item.value}
                onChange={(e) => updateItem(index, 'value', e.target.value)}
              />
              <Button
                size="sm"
                variant="secondary"
                onClick={() => removeItem(index)}
                aria-label={`删除偏好项 ${index + 1}`}
                type="button"
              >
                删除
              </Button>
            </div>
          ))}
          <Button variant="secondary" onClick={addItem} type="button">
            添加偏好
          </Button>
        </div>
      </Card>
    </div>
  );
}
