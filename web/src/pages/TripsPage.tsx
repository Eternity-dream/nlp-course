import { useState, useEffect } from 'react';
import { Plus, MapPin } from 'lucide-react';
import { api } from '../api';
import type { Trip } from '../types';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Loading } from '../components/Loading';
import { EmptyState } from '../components/EmptyState';
import { formatDate, formatCurrencyWhole } from '../utils';
import { formatApiError } from '../utils/errors';
import { navigate } from '../utils/router';
import { TripForm } from '../components/TripForm';
import './TripsPage.css';

export function TripsPage() {
  const [trips, setTrips] = useState<Trip[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState('');
  const [hasMore, setHasMore] = useState(true);
  const [offset, setOffset] = useState(0);
  const limit = 50;

  useEffect(() => {
    loadTrips();
  }, []);

  async function loadTrips(append = false) {
    const data = await api.listTrips(limit, append ? offset : 0).catch((err) => {
      setError(formatApiError(err));
      return null;
    });
    if (!data) {
      setLoading(false);
      return;
    }
    setTrips((items) =>
      append ? [...items, ...data.filter((trip) => !items.some((old) => old.id === trip.id))] : data
    );
    setHasMore(data.length === limit);
    setOffset(append ? offset + data.length : data.length);
    setLoading(false);
  }

  function navigateToTrip(tripId: string) {
    navigate(`/trips/${tripId}`);
  }

  if (loading) {
    return <Loading text="加载中" />;
  }

  if (showForm) {
    return (
      <div className="trips-page">
        <div className="trips-header">
          <h1 className="trips-title">新建行程</h1>
          <Button variant="secondary" onClick={() => setShowForm(false)}>
            取消
          </Button>
        </div>
        <TripForm
          onSuccess={(trip) => {
            setShowForm(false);
            navigateToTrip(trip.id);
          }}
          onCancel={() => setShowForm(false)}
        />
      </div>
    );
  }

  return (
    <div className="trips-page">
      <div className="trips-header">
        <h1 className="trips-title">我的行程</h1>
        <Button onClick={() => setShowForm(true)}>
          <Plus size={16} />
          新建行程
        </Button>
      </div>

      {error && <div className="trips-error">{error}</div>}

      {trips.length === 0 ? (
        <EmptyState
          icon={<MapPin size={24} />}
          title="暂无行程"
          action={
            <Button onClick={() => setShowForm(true)}>
              <Plus size={16} />
              新建行程
            </Button>
          }
        />
      ) : (
        <>
          <div className="trips-grid">
            {trips.map((trip) => (
              <Card key={trip.id} onClick={() => navigateToTrip(trip.id)} hoverable>
                <div className="trip-card-header">
                  <h3 className="trip-card-destination">{trip.destination}</h3>
                </div>
                {trip.start_date && trip.end_date && (
                  <div className="trip-card-dates">
                    {formatDate(trip.start_date)} 至 {formatDate(trip.end_date)}
                  </div>
                )}
                {(trip.travelers != null || trip.budget_cents != null) && (
                  <div className="trip-card-meta">
                    {[
                      trip.travelers != null ? `${trip.travelers}人` : null,
                      trip.budget_cents != null
                        ? `预算 ${formatCurrencyWhole(trip.budget_cents)}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                )}
              </Card>
            ))}
          </div>
          {hasMore && (
            <div className="trips-more">
              <Button variant="secondary" onClick={() => loadTrips(true)}>
                加载更多
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
