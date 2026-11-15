import { Loader2 } from 'lucide-react';
import './Loading.css';

interface LoadingProps {
  text?: string;
  size?: 'sm' | 'md' | 'lg';
}

export function Loading({ text, size = 'md' }: LoadingProps) {
  const sizeMap = { sm: 16, md: 24, lg: 32 };
  return (
    <div className="loading">
      <Loader2 size={sizeMap[size]} className="loading-spinner" />
      {text && <div className="loading-text">{text}</div>}
    </div>
  );
}
