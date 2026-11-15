import './Badge.css';

interface BadgeProps {
  children: React.ReactNode;
  variant?: 'default' | 'success' | 'warning' | 'danger' | 'info';
  showDot?: boolean;
  className?: string;
}

export function Badge({ children, variant = 'default', showDot = true, className = '' }: BadgeProps) {
  return (
    <span className={`badge badge-${variant} ${className}`}>
      {showDot && <span className="badge-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
