import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { 
  ArrowLeft, FolderSearch, Activity, ToggleLeft, 
  ToggleRight, Save, HardDrive, Bell, ShieldCheck, Loader2
} from 'lucide-react';
import api from '../services/api';
import toast from 'react-hot-toast';
import { motion } from 'framer-motion';
import { useCollectionStore } from '../store/collectionStore';

const Settings = () => {
  const navigate = useNavigate();
  const { collections } = useCollectionStore();
  
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  
  // Settings State
  const [settings, setSettings] = useState({
    watched_folder_path: '',
    auto_index_enabled: false,
    target_collection_id: '',
    stats_24h: 0 // Received from backend
  });

  // --- 1. FETCH CURRENT SETTINGS ---
  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const res = await api.get('/settings/'); // You'll need to build this endpoint
        if (res.data) setSettings(res.data);
      } catch (err) {
        console.log("No settings found, using defaults.");
      } finally {
        setLoading(false);
      }
    };
    fetchSettings();
  }, []);

  // --- 2. ELECTRON BRIDGE: BROWSE FOLDER ---
  const handleBrowse = async () => {
    // Check if we are running inside Electron
    if (window.electronAPI && window.electronAPI.selectFolder) {
      const path = await window.electronAPI.selectFolder();
      if (path) {
        setSettings({ ...settings, watched_folder_path: path });
        toast.success("Folder path locked in!");
      }
    } else {
      // Fallback for Web Browser (Manual entry)
      const path = prompt("Enter full absolute path to folder (Desktop App required for picker):");
      if (path) setSettings({ ...settings, watched_folder_path: path });
    }
  };

  // --- 3. SAVE TO MONGODB ---
  const handleSave = async () => {
    if (settings.auto_index_enabled && (!settings.watched_folder_path || !settings.target_collection_id)) {
      return toast.error("Please select a folder and a target collection!");
    }

    setSaving(true);
    try {
      await api.post('/settings/update/', settings); // You'll need to build this endpoint
      toast.success("Automation settings synced to Cloud.");
    } catch (err) {
      toast.error("Failed to sync settings.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="min-h-screen flex items-center justify-center bg-slate-50"><Loader2 className="animate-spin text-brand" /></div>;

  return (
    <div className="min-h-screen bg-[#F8FAFC]">
      <header className="bg-white border-b border-slate-200 sticky top-0 z-50 px-8 py-5 flex items-center justify-between">
        <div className="flex items-center gap-5">
          <Link to="/" className="p-2.5 hover:bg-slate-50 rounded-2xl transition-all">
            <ArrowLeft className="text-slate-400" />
          </Link>
          <h1 className="text-2xl font-black text-slate-900 tracking-tight">System Settings</h1>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-8 py-12">
        
        {/* SECTION: VAULT AUTOMATION */}
        <div className="space-y-8">
          <div className="flex items-center gap-4 mb-2">
            <div className="bg-brand/10 p-3 rounded-2xl text-brand"><FolderSearch /></div>
            <div>
              <h2 className="text-xl font-black text-slate-800">Vault Automation</h2>
              <p className="text-xs font-bold text-slate-400 uppercase tracking-widest">Auto-Indexing & Watchdog</p>
            </div>
          </div>

          <motion.div 
            initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
            className="bg-white rounded-[40px] border border-slate-100 p-10 shadow-sm space-y-10"
          >
            {/* 1. ENABLE TOGGLE */}
            <div className="flex items-center justify-between p-6 bg-slate-50 rounded-3xl border border-slate-100">
              <div className="flex items-center gap-4">
                <div className={`p-2 rounded-xl ${settings.auto_index_enabled ? 'bg-emerald-100 text-emerald-600' : 'bg-slate-200 text-slate-500'}`}>
                  <Activity className={settings.auto_index_enabled ? 'animate-pulse' : ''} />
                </div>
                <div>
                  <p className="font-black text-slate-800">Watchdog Status</p>
                  <p className="text-[10px] font-bold text-slate-400 uppercase">{settings.auto_index_enabled ? 'Actively Monitoring' : 'Offline'}</p>
                </div>
              </div>
              <button 
                onClick={() => setSettings({...settings, auto_index_enabled: !settings.auto_index_enabled})}
                className="cursor-pointer transition-all"
              >
                {settings.auto_index_enabled ? 
                  <ToggleRight className="w-12 h-12 text-brand fill-brand/10" /> : 
                  <ToggleLeft className="w-12 h-12 text-slate-300" />
                }
              </button>
            </div>

            {/* 2. FOLDER PATH */}
            <div className="space-y-3">
              <label className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-400 ml-2">Monitor Directory</label>
              <div className="flex gap-3">
                <div className="flex-1 bg-slate-50 border-2 border-slate-100 rounded-2xl px-5 py-4 font-bold text-slate-600 truncate">
                  {settings.watched_folder_path || "No directory selected..."}
                </div>
                <button 
                  onClick={handleBrowse}
                  className="bg-slate-900 text-white px-6 rounded-2xl font-black text-xs hover:bg-brand transition-all cursor-pointer"
                >
                  Browse
                </button>
              </div>
            </div>

            {/* 3. TARGET COLLECTION */}
            <div className="space-y-3">
              <label className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-400 ml-2">Target Collection</label>
              <select 
                value={settings.target_collection_id}
                onChange={(e) => setSettings({...settings, target_collection_id: e.target.value})}
                className="w-full bg-slate-50 border-2 border-slate-100 rounded-2xl px-5 py-4 font-bold text-slate-800 outline-none focus:border-brand transition-all"
              >
                <option value="">Select a Vault Room...</option>
                {collections.map(col => (
                  <option key={col._id} value={col._id}>{col.name}</option>
                ))}
              </select>
              <p className="text-[10px] text-slate-400 font-medium ml-2">New files detected by the Watchdog will be ingested here.</p>
            </div>

            {/* 4. ACTIVITY STATS */}
            <div className="pt-6 border-t border-slate-50 flex items-center justify-between">
               <div className="flex items-center gap-2">
                  <ShieldCheck className="text-emerald-500 w-4 h-4" />
                  <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Automation Logs</span>
               </div>
               <p className="text-xs font-black text-slate-900">
                  <span className="text-brand">{settings.stats_24h}</span> Files auto-indexed (24h)
               </p>
            </div>
          </motion.div>

          <button 
            onClick={handleSave}
            disabled={saving}
            className="w-full bg-brand text-white py-5 rounded-[32px] font-black text-lg border-2 border-slate-900 shadow-[8px_8px_0px_0px_rgba(15,23,42,1)] active:translate-x-[4px] active:translate-y-[4px] active:shadow-none transition-all flex items-center justify-center gap-3 disabled:opacity-50"
          >
            {saving ? <Loader2 className="animate-spin" /> : <Save />}
            Sync System Settings
          </button>
        </div>

      </main>
    </div>
  );
};

export default Settings;