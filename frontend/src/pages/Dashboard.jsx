import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { useAuthStore } from '../store/authStore';
import { useCollectionStore } from '../store/collectionStore';
import api from '../services/api';
import { 
  Plus, Folder, LogOut, LayoutGrid, Search, 
  Camera, Film, Mic, FileText, Book, X, Check
} from 'lucide-react';
import toast from 'react-hot-toast';

// --- ICONS MAPPING ---
// Intuition: We store a string in DB ("camera") and map it to a component here.
const ICON_OPTIONS = [
  { id: 'folder', icon: Folder },
  { id: 'camera', icon: Camera },
  { id: 'film', icon: Film },
  { id: 'mic', icon: Mic },
  { id: 'file-text', icon: FileText },
  { id: 'book', icon: Book },
];

const PRESET_COLORS = [
  "#3b82f6", // Blue
  "#10b981", // Emerald
  "#f59e0b", // Amber
  "#ef4444", // Red
  "#8b5cf6", // Violet
  "#ec4899", // Pink
  "#0f172a", // Slate
];

const Dashboard = () => {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);
  const { collections, setCollections, addCollection } = useCollectionStore();
  
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
    
  const clearCollections = useCollectionStore((state) => state.clearStore);

  // Modal Form State
  const [newColData, setNewColData] = useState({
    name: '',
    description: '',
    theme_color: '#3b82f6',
    icon_tag: 'folder'
  });

  useEffect(() => {
    const fetchCollections = async () => {
      try {
        setLoading(true);
        const response = await api.get('/collections/');
        setCollections(response.data);
      } catch (error) {
        toast.error("Failed to load your vault.");
      } finally {
        setLoading(false);
      }
    };
    fetchCollections();
  }, [setCollections]);

    const handleLogout = () => {
        // 1. Wipe the Auth Store (Token & User)
        logout(); 
        
        // 2. Wipe the Collection Store (Folders)
        clearCollections(); 
        
        // 3. Success Message
        toast.success("See you soon! Vault locked. 🔒");
        
        // 4. Redirect (App.jsx handles this automatically because token becomes null)
    };

    // --- HELPER: Get User Initials (e.g., "John Doe" -> "JD") ---
    const getInitials = (name) => {
        if(!name) return "?";
        const parts = name.split(' ');
        if(parts.length > 1) return (parts[0][0] + parts[1][0]).toUpperCase();
        return parts[0][0].toUpperCase();
    };

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newColData.name.trim()) return toast.error("Name is required!");

    setIsCreating(true);
    try {
      const response = await api.post('/collections/create/', newColData);
      addCollection(response.data.collection); // Updates UI instantly
      toast.success("New Vault Room Created!");
      setIsModalOpen(false);
      setNewColData({ name: '', description: '', theme_color: '#3b82f6', icon_tag: 'folder' });
    } catch (error) {
      toast.error("Creation failed.");
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#F8FAFC]">
      {/* --- HEADER --- */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-brand/10 p-2 rounded-lg text-brand"><LayoutGrid /></div>
            <div>
              <h1 className="text-xl font-bold text-slate-900">Hey, {user?.name.split(' ')[0]} 👋</h1>
              <p className="text-xs text-slate-500 font-bold uppercase tracking-wider">Multimodal Vault</p>
            </div>
          </div>

          <div className="flex gap-4">
            <button 
              onClick={() => setIsModalOpen(true)}
              className="bg-brand cursor-pointer text-white px-5 py-2.5 rounded-xl font-bold text-sm border-2 border-slate-900 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-none transition-all flex items-center gap-2"
            >
              <Plus className="w-5 h-5" /> New Collection
            </button>
             <div className="flex items-center gap-2 pl-4 border-l border-slate-200">
                <div className="w-10 h-10 rounded-full bg-slate-900 text-white flex items-center justify-center font-black text-xs border-2 border-white shadow-sm">
                {getInitials(user?.name)}
                </div>
                
                <button 
                onClick={handleLogout}
                title="Logout"
                className="p-2 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-xl transition-all group"
                >
                <LogOut className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                </button>
            </div>
          </div>
        </div>
      </header>

      {/* --- MAIN GRID --- */}
      <main className="max-w-7xl mx-auto px-6 py-12">
        {loading ? (
            <div className="text-center py-20 text-slate-400 font-bold">Syncing Vault...</div>
        ) : collections.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
            {collections.map((col) => {
              // Get the icon component dynamically
              const IconComp = ICON_OPTIONS.find(i => i.id === col.icon_tag)?.icon || Folder;
              
              return (
                <motion.div 
                  key={col._id}
                  whileHover={{ y: -8 }} // Tactile "Lift" animation
                  onClick={() => navigate(`/collection/${col._id}`)}
                  className="group bg-white p-6 rounded-3xl border border-slate-200 hover:border-slate-900 shadow-sm transition-all cursor-pointer relative overflow-hidden"
                >
                  <div className="absolute top-0 left-0 w-full h-2" style={{ backgroundColor: col.theme_color }} />
                  
                  <div className="bg-slate-50 w-14 h-14 rounded-2xl flex items-center justify-center mb-5 group-hover:bg-slate-900 group-hover:text-white transition-colors">
                    <IconComp className="w-7 h-7" />
                  </div>
                  
                  <h3 className="font-black text-slate-800 text-xl mb-1">{col.name}</h3>
                  <p className="text-slate-500 text-sm line-clamp-2 min-h-[40px]">{col.description || "No description provided."}</p>
                  
                  <div className="mt-6 pt-4 border-t border-slate-50 flex items-center justify-between">
                    <span className="text-[10px] font-black uppercase text-slate-400 tracking-tighter">Enter Vault</span>
                    <div className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center group-hover:bg-brand group-hover:text-white transition-colors">
                        <Plus className="w-4 h-4 rotate-45" />
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </div>
        ) : (
          /* Empty State */
          <div className="text-center py-32 bg-white rounded-[40px] border-4 border-dashed border-slate-100">
             <Search className="w-16 h-16 text-slate-200 mx-auto mb-4" />
             <h2 className="text-2xl font-black text-slate-800">Your Brain is Empty</h2>
             <p className="text-slate-400 mb-8 mt-2">Start by creating a workspace for your projects.</p>
             <button onClick={() => setIsModalOpen(true)} className="bg-slate-900 text-white px-8 py-3 rounded-2xl font-bold">Create First Collection</button>
          </div>
        )}
      </main>

      {/* --- CREATE COLLECTION MODAL --- */}
      <AnimatePresence>
        {isModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
            <motion.div 
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                className="absolute inset-0 bg-slate-900/60 backdrop-blur-md" 
                onClick={() => setIsModalOpen(false)} 
            />
            
            <motion.div 
              initial={{ scale: 0.9, y: 20 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.9, y: 20 }}
              className="bg-white w-full max-w-lg rounded-[32px] shadow-2xl relative z-10 overflow-hidden"
            >
              <div className="p-10">
                <div className="flex justify-between items-center mb-8">
                    <h2 className="text-3xl font-black text-slate-900 tracking-tight text-center">Architect a Vault</h2>
                    <button onClick={() => setIsModalOpen(false)} className="p-2 hover:bg-slate-100 rounded-full"><X /></button>
                </div>

                <form onSubmit={handleCreate} className="space-y-6">
                  <div>
                    <label className="text-xs font-black uppercase tracking-widest text-slate-400 mb-2 block">Name your Workspace</label>
                    <input autoFocus className="w-full px-5 py-4 bg-slate-50 border-2 border-slate-100 rounded-2xl outline-none focus:border-brand transition-all font-bold" placeholder="e.g. AI Research" onChange={(e) => setNewColData({...newColData, name: e.target.value})} />
                  </div>
                
                    <div>
                        <label className="text-xs font-black uppercase tracking-widest text-slate-400 mb-2 block">Workspace Story</label>
                        <textarea 
                            className="w-full px-5 py-4 bg-slate-50 border-2 border-slate-100 rounded-2xl outline-none focus:border-brand transition-all font-bold min-h-[100px] resize-none text-sm" 
                            placeholder="What kind of magic will live here? (Optional)" 
                            value={newColData.description}
                            onChange={(e) => setNewColData({...newColData, description: e.target.value})} 
                        />
                    </div>

                  <div>
                    <label className="text-xs font-black uppercase tracking-widest text-slate-400 mb-2 block">Icon & Mood</label>
                    <div className="flex gap-3 flex-wrap">
                      {ICON_OPTIONS.map(({ id, icon: Icon }) => (
                        <button key={id} type="button" onClick={() => setNewColData({...newColData, icon_tag: id})} className={`p-4 rounded-2xl border-2 transition-all ${newColData.icon_tag === id ? 'border-brand bg-brand/5 text-brand shadow-lg' : 'border-slate-50 bg-slate-50 text-slate-400 hover:border-slate-200'}`}>
                          <Icon className="w-6 h-6" />
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="text-xs font-black uppercase tracking-widest text-slate-400 mb-2 block">Visual Theme</label>
                    <div className="flex gap-4">
                      {PRESET_COLORS.map(color => (
                        <button key={color} type="button" onClick={() => setNewColData({...newColData, theme_color: color})} className={`w-10 h-10 rounded-full border-4 transition-all ${newColData.theme_color === color ? 'border-slate-900 scale-125' : 'border-white shadow-sm'}`} style={{ backgroundColor: color }}>
                          {newColData.theme_color === color && <Check className="w-4 h-4 text-white mx-auto" />}
                        </button>
                      ))}
                    </div>
                  </div>

                  <button disabled={isCreating} type="submit" className="w-full bg-brand cursor-pointer text-white py-5 rounded-2xl font-black text-lg border-2 border-slate-900 shadow-[6px_6px_0px_0px_rgba(15,23,42,1)] active:shadow-none active:translate-x-[4px] active:translate-y-[4px] transition-all disabled:opacity-50">
                    {isCreating ? 'Finalizing Vault...' : 'Create Workspace →'}
                  </button>
                </form>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default Dashboard;