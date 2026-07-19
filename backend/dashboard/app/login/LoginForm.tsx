'use client';

import { useState } from 'react';
import { signIn } from 'next-auth/react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowRight } from 'lucide-react';

export default function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const callbackUrl = params.get('callbackUrl') ?? '/';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const result = await signIn('credentials', { email, password, redirect: false });
    setPending(false);
    if (result?.error) {
      setError('Invalid email or password.');
      return;
    }
    router.push(callbackUrl);
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} className="mt-8 space-y-4">
      <label className="block">
        <span className="block text-caption font-medium text-navy mb-1.5">Email</span>
        <input
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full px-3 py-2.5 border border-border rounded-md bg-surface text-body text-navy"
        />
      </label>

      <label className="block">
        <span className="block text-caption font-medium text-navy mb-1.5">Password</span>
        <input
          type="password"
          required
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full px-3 py-2.5 border border-border rounded-md bg-surface text-body text-navy"
        />
      </label>

      {error ? (
        <p role="alert" className="text-caption text-danger">
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 btn-primary font-medium transition-colors disabled:opacity-60"
      >
        {pending ? 'Signing in…' : 'Sign in'}
        <ArrowRight size={16} aria-hidden />
      </button>

      <div className="relative py-2 text-center">
        <span className="relative z-10 bg-surface-raised px-3 text-caption text-slate">or</span>
        <span className="absolute inset-x-0 top-1/2 border-t border-border-light" aria-hidden />
      </div>

      <button
        type="button"
        onClick={() => signIn('auth0', { callbackUrl })}
        className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 border border-border rounded-md bg-surface text-body font-medium text-navy transition-colors hover:bg-surface-muted"
      >
        Sign in with SSO
      </button>
    </form>
  );
}
