import { classNames } from '../utils';
import './Button.css';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger';
  size?: 'sm' | 'md';
  loading?: boolean;
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled,
  className,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={classNames(
        'button',
        `button-${variant}`,
        `button-${size}`,
        loading && 'button-loading',
        className
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? '处理中...' : children}
    </button>
  );
}
