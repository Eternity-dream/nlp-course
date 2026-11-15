import { useId } from 'react';
import './Input.css';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  help?: string;
}

export function Input({ label, error, help, className, id: providedId, ...props }: InputProps) {
  const generatedId = useId();
  const id = providedId || generatedId;
  const errorId = `${id}-error`;
  const helpId = `${id}-help`;

  return (
    <div className="input-wrapper">
      {label && (
        <label className="input-label" htmlFor={id}>
          {label}
        </label>
      )}
      <input
        id={id}
        className={`input ${error ? 'input-error' : ''} ${className || ''}`}
        aria-invalid={error ? 'true' : 'false'}
        aria-describedby={error ? errorId : help ? helpId : undefined}
        {...props}
      />
      {error && (
        <div className="input-error-text" id={errorId}>
          {error}
        </div>
      )}
      {help && !error && (
        <div className="input-help" id={helpId}>
          {help}
        </div>
      )}
    </div>
  );
}

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  help?: string;
}

export function Textarea({ label, error, help, className, id: providedId, ...props }: TextareaProps) {
  const generatedId = useId();
  const id = providedId || generatedId;
  const errorId = `${id}-error`;
  const helpId = `${id}-help`;

  return (
    <div className="input-wrapper">
      {label && (
        <label className="input-label" htmlFor={id}>
          {label}
        </label>
      )}
      <textarea
        id={id}
        className={`input textarea ${error ? 'input-error' : ''} ${className || ''}`}
        aria-invalid={error ? 'true' : 'false'}
        aria-describedby={error ? errorId : help ? helpId : undefined}
        {...props}
      />
      {error && (
        <div className="input-error-text" id={errorId}>
          {error}
        </div>
      )}
      {help && !error && (
        <div className="input-help" id={helpId}>
          {help}
        </div>
      )}
    </div>
  );
}

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
}

export function Select({ label, error, children, className, id: providedId, ...props }: SelectProps) {
  const generatedId = useId();
  const id = providedId || generatedId;
  const errorId = `${id}-error`;

  return (
    <div className="input-wrapper">
      {label && (
        <label className="input-label" htmlFor={id}>
          {label}
        </label>
      )}
      <select
        id={id}
        className={`input ${error ? 'input-error' : ''} ${className || ''}`}
        aria-invalid={error ? 'true' : 'false'}
        aria-describedby={error ? errorId : undefined}
        {...props}
      >
        {children}
      </select>
      {error && (
        <div className="input-error-text" id={errorId}>
          {error}
        </div>
      )}
    </div>
  );
}
