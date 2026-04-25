'use client';

import { useState, type FormEvent } from 'react';
import { CheckCircle2, AlertCircle } from 'lucide-react';
import Button from './Button';

const roles = [
  'Bank executive',
  'Investor',
  'Potential advisor',
  'Engineer or candidate',
  'Journalist',
  'Other',
];

type Status = 'idle' | 'submitting' | 'success' | 'error';

const inputClasses =
  'w-full rounded-md border border-border-light bg-white px-4 py-3 text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/40 transition disabled:opacity-60 disabled:cursor-not-allowed';

export default function ContactForm() {
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setStatus('submitting');
    setError(null);

    const form = e.currentTarget;
    const data = new FormData(form);
    const payload = {
      name: data.get('name'),
      email: data.get('email'),
      role: data.get('role'),
      message: data.get('message'),
      _gotcha: data.get('_gotcha'),
    };

    try {
      const res = await fetch('/api/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const json = await res.json().catch(() => ({}));

      if (res.ok) {
        setStatus('success');
        form.reset();
        return;
      }

      setError(
        json?.error ??
          'Something went wrong. Please email eric@aequoros.com directly.'
      );
      setStatus('error');
    } catch {
      setError(
        'Network error. Please check your connection or email eric@aequoros.com directly.'
      );
      setStatus('error');
    }
  };

  if (status === 'success') {
    return (
      <div
        role="status"
        aria-live="polite"
        className="bg-white border border-border-light border-l-4 border-l-accent rounded-lg p-8"
      >
        <div className="flex items-start gap-3">
          <CheckCircle2
            size={24}
            className="text-accent shrink-0 mt-0.5"
            aria-hidden
          />
          <div>
            <h3 className="font-serif font-bold text-navy text-xl">
              Message received.
            </h3>
            <p className="mt-3 text-text-primary leading-relaxed">
              Thanks for reaching out. We typically respond within 24-48 hours.
              All inquiries kept confidential.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const submitting = status === 'submitting';

  return (
    <form
      onSubmit={onSubmit}
      className="bg-white border border-border-light border-l-4 border-l-accent rounded-lg p-8 space-y-5"
    >
      <input
        type="text"
        name="_gotcha"
        tabIndex={-1}
        autoComplete="off"
        className="hidden"
        aria-hidden
      />

      <div>
        <label
          htmlFor="name"
          className="block text-sm font-medium text-text-primary mb-2"
        >
          Name
        </label>
        <input
          id="name"
          name="name"
          type="text"
          required
          autoComplete="name"
          maxLength={100}
          className={inputClasses}
          placeholder="Your full name"
          disabled={submitting}
        />
      </div>

      <div>
        <label
          htmlFor="email"
          className="block text-sm font-medium text-text-primary mb-2"
        >
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="email"
          maxLength={200}
          className={inputClasses}
          placeholder="you@example.com"
          disabled={submitting}
        />
      </div>

      <div>
        <label
          htmlFor="role"
          className="block text-sm font-medium text-text-primary mb-2"
        >
          Role
        </label>
        <select
          id="role"
          name="role"
          className={inputClasses}
          defaultValue=""
          disabled={submitting}
          required
        >
          <option value="" disabled>
            Select one
          </option>
          {roles.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label
          htmlFor="message"
          className="block text-sm font-medium text-text-primary mb-2"
        >
          Message
        </label>
        <textarea
          id="message"
          name="message"
          rows={5}
          required
          maxLength={5000}
          className={`${inputClasses} resize-y`}
          placeholder="What would you like to discuss?"
          disabled={submitting}
        />
      </div>

      {status === 'error' && error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-800"
        >
          <AlertCircle size={18} className="shrink-0 mt-0.5" aria-hidden />
          <p>{error}</p>
        </div>
      )}

      <Button type="submit" disabled={submitting}>
        {submitting ? 'Sending…' : 'Send message'}
      </Button>

      <p className="text-xs text-text-muted pt-2">
        Responses typically within 24-48 hours. All inquiries kept confidential.
      </p>
    </form>
  );
}
