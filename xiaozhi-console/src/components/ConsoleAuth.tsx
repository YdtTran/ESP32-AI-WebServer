import { FormEvent, useEffect, useState } from 'react';
import { App } from '../App';
import { authenticate, currentUser, logout, setSessionExpiredHandler } from '../api/client';

export function ConsoleAuth() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [user, setUser] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    setSessionExpiredHandler(() => setUser(null));
    currentUser().then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
    return () => setSessionExpiredHandler(() => {});
  }, []);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      await authenticate(username, password);
      setUser(await currentUser());
      setPassword('');
    } catch {
      setError('Unable to sign in. Check your credentials and try again.');
    } finally {
      setLoading(false);
    }
  }

  if (loading && !user) return <main className="p-8">Loading…</main>;
  if (user) return <><div className="flex justify-end border-b bg-background px-6 py-2 text-sm"><span className="mr-4">{user}</span><button onClick={async () => { try { await logout(); } finally { setUser(null); } }} className="underline">Sign out</button></div><App /></>;
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
      <form onSubmit={signIn} className="w-full max-w-sm space-y-5 rounded-xl border bg-card p-8 shadow-sm">
        <div><h1 className="text-2xl font-semibold">Sign in to the console</h1><p className="mt-2 text-sm text-muted-foreground">Use the account provided by your administrator.</p></div>
        <label className="block text-sm font-medium">Username<input className="mt-1 block w-full rounded-md border bg-background px-3 py-2" value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" required /></label>
        <label className="block text-sm font-medium">Password<input className="mt-1 block w-full rounded-md border bg-background px-3 py-2" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required /></label>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <button disabled={loading} className="w-full rounded-md bg-primary px-4 py-2 text-primary-foreground disabled:opacity-50">Sign in</button>
      </form>
    </main>
  );
}
