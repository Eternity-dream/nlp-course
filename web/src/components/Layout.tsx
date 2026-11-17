import { useState, useEffect } from 'react';
import './Layout.css';

interface LayoutProps {
  children: React.ReactNode;
}

export function Layout({ children }: LayoutProps) {
  const [currentPath, setCurrentPath] = useState(window.location.pathname);

  useEffect(() => {
    const handlePopState = () => {
      setCurrentPath(window.location.pathname);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigate = (path: string) => {
    window.history.pushState({}, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  const navLinks = [
    { path: '/', label: '行程' },
    { path: '/settings', label: '设置' },
  ];

  const active = (path: string) => path === '/'
    ? currentPath === '/' || currentPath.startsWith('/trips')
    : ['/settings', '/knowledge', '/preferences', '/status'].some((prefix) => currentPath.startsWith(prefix));

  return (
    <div className="layout">
      <header className="layout-header">
        <div className="layout-header-content">
          <div className="layout-logo">旅行规划</div>
          <nav className="layout-nav">
            {navLinks.map((link) => (
              <a
                key={link.path}
                href={link.path}
                className={`layout-nav-link ${active(link.path) ? 'active' : ''}`}
                aria-current={active(link.path) ? 'page' : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(link.path);
                }}
              >
                {link.label}
              </a>
            ))}
          </nav>
        </div>
      </header>
      <main className="layout-main">{children}</main>
    </div>
  );
}
