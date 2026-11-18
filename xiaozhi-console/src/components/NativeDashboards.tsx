import { useEffect, useState } from 'react';

const api = window.location.port === '5173' ? 'http://localhost:8005/api/native-dashboards' : '/api/native-dashboards';

type Device = { id: string; name: string; type: string; telemetry: Record<string, { value: string; ts: number }[]>; attributes: Record<string, string | number | boolean> };
type Data = { dashboards: { id: string; title: string }[]; devices: Device[] };
const fields = [
  ['temperatureAlarmFlag', 'High temperature alarm', 'checkbox'],
  ['temperatureAlarmThreshold', 'High temperature threshold (°C)', 'number'],
  ['humidityAlarmFlag', 'Low humidity alarm', 'checkbox'],
  ['humidityAlarmThreshold', 'Low humidity threshold (%)', 'number'],
] as const;

export function NativeDashboards() {
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [data, setData] = useState<Data | null>(null);
  const [selected, setSelected] = useState('Thermostats');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState('');

  async function load() {
    try {
      const response = await fetch(api, { headers: { Authorization: `Bearer ${token}` } });
      if (response.status === 401) { setToken(''); throw new Error('Session expired. Please sign in again.'); }
      if (!response.ok) throw new Error('Unable to load ThingsBoard data.');
      setData(await response.json());
      setError('');
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load data.'); }
  }

  useEffect(() => {
    if (!token) return;
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, [token]);

  async function login(e: React.FormEvent) {
    e.preventDefault();
    try {
      const response = await fetch(`${api}/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
      if (!response.ok) throw new Error(response.status === 503 ? 'Console account is not configured on the server.' : response.status === 429 ? 'Too many sign-in attempts. Try again in five minutes.' : 'Invalid console username or password.');
      const result = await response.json();
      setToken(result.token); setPassword(''); setError('');
    } catch (e) { setError(e instanceof Error ? e.message : 'Sign-in failed.'); }
  }

  async function update(device: Device, key: string, value: boolean | number) {
    setSaving(device.id);
    try {
      const response = await fetch(`${api}/thermostats/${device.id}/settings`, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ [key]: value }) });
      if (!response.ok) throw new Error('Could not update the setting in ThingsBoard.');
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Update failed.'); }
    finally { setSaving(''); }
  }

  async function logout() {
    await fetch(`${api}/logout`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
    setToken(''); setData(null);
  }

  const devices = data?.devices.filter(device => selected !== 'Thermostats' || device.type.toLowerCase() === 'thermostat') || [];
  return <section className="rounded-xl border bg-card p-5 shadow-xs space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-sm font-semibold">ThingsBoard dashboards</h2><p className="text-xs text-muted-foreground">Native device data refreshes every 15 seconds.</p></div>
      {token && <button onClick={logout} className="rounded-lg border px-3 py-2 text-xs">Sign out</button>}
    </div>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {!token ? <form onSubmit={login} className="flex flex-wrap items-end gap-3">
      <label className="text-xs">Console username<input required autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} className="mt-1 block rounded-md border bg-background px-3 py-2 text-sm" /></label>
      <label className="text-xs">Console password<input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} className="mt-1 block rounded-md border bg-background px-3 py-2 text-sm" /></label>
      <button className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">Sign in</button>
    </form> : <>
      <label className="block text-xs">Dashboard<select value={selected} onChange={e => setSelected(e.target.value)} className="ml-3 rounded-md border bg-background px-3 py-2 text-sm">
        {(data?.dashboards || []).map(d => <option key={d.id} value={d.title}>{d.title}</option>)}
      </select></label>
      {selected !== 'Thermostats' && <p className="text-xs text-muted-foreground">Device data is shown below. Dashboard-specific charts and controls are not yet available.</p>}
      {!data ? <p className="text-sm">Loading…</p> : devices.length === 0 ? <p className="text-sm text-muted-foreground">No {selected === 'Thermostats' ? 'thermostat devices' : 'devices'} found.</p> : devices.map(device => <article key={device.id} className="rounded-lg border p-4 space-y-3">
        <div><h3 className="text-sm font-semibold">{device.name}</h3><p className="text-xs text-muted-foreground">{device.type}</p></div>
        <div className="flex flex-wrap gap-3">{Object.entries(device.telemetry || {}).map(([key, samples]) => <div key={key} className="rounded-md bg-muted px-3 py-2 text-xs"><span className="text-muted-foreground">{key}: </span>{samples[0]?.value ?? '—'}</div>)}</div>
        {Object.keys(device.telemetry || {}).length === 0 && <p className="text-xs text-muted-foreground">No telemetry available.</p>}
        {selected !== 'Thermostats' && Object.entries(device.attributes || {}).map(([key, value]) => <div key={key} className="text-xs"><span className="text-muted-foreground">{key}: </span>{String(value)}</div>)}
        {selected === 'Thermostats' && <div className="grid gap-3 sm:grid-cols-2">{fields.map(([key, label, type]) => <label key={key} className="text-xs flex items-center justify-between gap-2">{label}
          {type === 'checkbox' ? <input type="checkbox" disabled={saving === device.id} checked={device.attributes[key] === true} onChange={e => void update(device, key, e.target.checked)} /> : <input type="number" step="any" disabled={saving === device.id} defaultValue={String(device.attributes[key] ?? '')} key={`${device.id}-${key}-${device.attributes[key] ?? ''}`} onBlur={e => { if (e.target.value !== '' && Number(e.target.value) !== device.attributes[key]) void update(device, key, Number(e.target.value)); }} className="w-24 rounded-md border bg-background px-2 py-1" />}
        </label>)}</div>}
      </article>)}
    </>}
  </section>;
}
