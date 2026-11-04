import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api, { explainApiError } from '../services/api';
import toast from 'react-hot-toast';

export default function LinkedSources() {
  const [sources, setSources] = useState([]);
  const [collections, setCollections] = useState([]);
  const [root, setRoot] = useState('');
  const [collection, setCollection] = useState('');
  const [exclusions, setExclusions] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [patterns, setPatterns] = useState('');

  useEffect(() => {
    let alive = true;
    let timer;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const result = await api.get('/sources/', { signal: controller.signal });
        if (alive) { setSources(result.data.filter(source => source.kind === 'linked_folder')); setError(''); }
      } catch (failure) {
        if (alive && !controller.signal.aborted) setError(explainApiError(failure));
      } finally {
        if (alive) timer = setTimeout(refresh, 3000);
      }
    };
    api.get('/collections/', { signal: controller.signal }).then(result => {
      if (alive) setCollections(result.data);
    }).catch(failure => { if (alive && !controller.signal.aborted) setError(explainApiError(failure)); });
    refresh();
    return () => { alive = false; clearTimeout(timer); controller.abort(); };
  }, []);

  const register = async event => {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await api.post('/sources/', { root_path: root,
        collection_id: collection || null, exclusions: exclusions.split('\n').map(value => value.trim()).filter(Boolean) });
      setSources(previous => [...previous, result.data]);
      setRoot('');
      toast.success('Folder linked. Waiting for the next scan.');
    } catch (failure) { toast.error(explainApiError(failure)); }
    finally { setBusy(false); }
  };

  const control = async (source, action) => {
    try {
      const result = await api.patch(`/sources/${source.id}/`, { action });
      setSources(previous => previous.map(item => item.id === source.id ? result.data : item));
    } catch (failure) { toast.error(explainApiError(failure)); }
  };

  const saveExclusions = async source => {
    try {
      const result = await api.patch(`/sources/${source.id}/`, {
        exclusions: patterns.split('\n').map(value => value.trim()).filter(Boolean)
      });
      setSources(previous => previous.map(item => item.id === source.id ? result.data : item));
      setEditing(null);
    } catch (failure) { toast.error(explainApiError(failure)); }
  };

  return <main className="max-w-3xl mx-auto p-8 space-y-6">
    <Link to="/" className="text-brand">Back to gallery</Link>
    <h1 className="text-2xl font-bold">Linked folders</h1>
    <p className="text-slate-600">Files remain in their original folders. Manual uploads remain available inside each collection.</p>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    <form onSubmit={register} className="bg-white rounded-2xl border p-6 space-y-4">
      <label className="block">Folder on this computer
        <input required value={root} onChange={event => setRoot(event.target.value)}
          placeholder="C:\Users\YourName\Pictures" className="block w-full border rounded-lg p-3 mt-2" />
      </label>
      <label className="block">Collection
        <select value={collection} onChange={event => setCollection(event.target.value)} className="block w-full border rounded-lg p-3 mt-2">
          <option value="">No collection</option>
          {collections.map(item => <option key={item._id} value={item._id}>{item.name}</option>)}
        </select>
      </label>
      <label className="block">Exclude names or paths (one pattern per line)
        <textarea value={exclusions} onChange={event => setExclusions(event.target.value)}
          placeholder="private/*" className="block w-full border rounded-lg p-3 mt-2" />
      </label>
      <button disabled={busy} className="bg-slate-900 text-white rounded-lg px-5 py-3">{busy ? 'Linking…' : 'Link folder'}</button>
    </form>
    {sources.map(source => <section key={source.id} className="bg-white rounded-2xl border p-6 space-y-3">
      <h2 className="font-semibold break-all">{source.root_path}</h2>
      <p role="status">{source.paused ? 'Paused' : source.last_scan_at ? source.state : 'Waiting for initial scan'}</p>
      {source.error && <p className="text-amber-700">{source.error}</p>}
      <p className="text-sm text-slate-500">Last scan: {source.last_scan_at ? new Date(source.last_scan_at).toLocaleString() : 'Not yet scanned'}</p>
      <div className="flex gap-4">
        <button onClick={() => control(source, source.paused ? 'resume' : 'pause')} className="border rounded-lg px-4 py-2">{source.paused ? 'Resume' : 'Pause'}</button>
        <button disabled={source.paused} onClick={() => control(source, 'scan')} className="border rounded-lg px-4 py-2">Scan now</button>
        <button onClick={() => { setEditing(source.id); setPatterns(source.exclusions.join('\n')); }} className="border rounded-lg px-4 py-2">Edit exclusions</button>
      </div>
      {editing === source.id && <div className="space-y-2">
        <textarea aria-label="Exclusion patterns" value={patterns} onChange={event => setPatterns(event.target.value)} className="w-full border rounded-lg p-3" />
        <button onClick={() => saveExclusions(source)} className="border rounded-lg px-4 py-2">Save exclusions</button>
      </div>}
    </section>)}
  </main>;
}
