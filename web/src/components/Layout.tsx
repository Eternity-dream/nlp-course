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
    { path: '/knowledge', label: '资料' },
    { path: '/preferences', label: '偏好' },
    { path: '/status', label: '服务' },
  ];

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
                className={`layout-nav-link ${currentPath === link.path ? 'active' : ''}`}
                aria-current={currentPath === link.path ? 'page' : undefined}
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
