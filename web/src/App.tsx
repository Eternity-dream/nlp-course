import { useState, useEffect } from 'react';
import { SettingsPage } from './pages/SettingsPage';
import { Layout } from './components/Layout';
import { TripsPage } from './pages/TripsPage';
import { TripDetailPage } from './pages/TripDetailPage';
import { KnowledgePage } from './pages/KnowledgePage';
import { PreferencesPage } from './pages/PreferencesPage';
import { StatusPage } from './pages/StatusPage';

type Route =
  | { page: 'settings' }
  | { page: 'trips' }
  | { page: 'trip-detail'; tripId: string }
  | { page: 'knowledge' }
  | { page: 'preferences' }
  | { page: 'status' };

function parseRoute(): Route {
  const path = window.location.pathname;
  if (path === '/' || path === '/trips') return { page: 'trips' };
  if (path.startsWith('/trips/')) return { page: 'trip-detail', tripId: path.split('/')[2] };
  if (path === '/settings') return { page: 'settings' };
  if (path === '/knowledge') return { page: 'knowledge' };
  if (path === '/preferences') return { page: 'preferences' };
  if (path === '/status') return { page: 'status' };
  return { page: 'trips' };
}

export function App() {
  const [route, setRoute] = useState<Route>(parseRoute());

  useEffect(() => {
    const handlePopState = () => {
      setRoute(parseRoute());
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  let content: React.ReactNode;
  switch (route.page) {
    case 'settings':
      content = <SettingsPage />;
      break;
    case 'trips':
      content = <TripsPage />;
      break;
    case 'trip-detail':
      content = <TripDetailPage key={route.tripId} tripId={route.tripId} />;
      break;
    case 'knowledge':
      content = <KnowledgePage />;
      break;
    case 'preferences':
      content = <PreferencesPage />;
      break;
    case 'status':
      content = <StatusPage />;
      break;
  }

  return <Layout>{content}</Layout>;
}
