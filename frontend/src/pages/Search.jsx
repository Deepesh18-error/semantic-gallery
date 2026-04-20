import React, { useState, useEffect, useCallback, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Search as SearchIcon, Mic, MicOff, Filter, 
  Image as ImageIcon, Video, Music, FileText, 
  X, Loader2, Sparkles, History, ArrowRight
} from 'lucide-react';
import api from '../services/api';
import toast from 'react-hot-toast';
import { useSearchParams } from 'react-router-dom'; 
import SearchResultCard from './SearchResultCard'; 
import SearchResultModal from './SearchResultModal'; 
// --- CONSTANTS ---
const FILTERS = [
  { id: 'ALL', label: 'All', icon: Filter },
  { id: 'IMAGE', label: 'Images', icon: ImageIcon },
  { id: 'VIDEO', label: 'Videos', icon: Video },
  { id: 'AUDIO', label: 'Audio', icon: Music },
  { id: 'DOCUMENT', label: 'Documents', icon: FileText },
];


const Search = () => {
  // --- STATE ---
  const [searchParams] = useSearchParams();
  const [query, setQuery] = useState(searchParams.get('q') || '');
  const [activeFilter, setActiveFilter] = useState('ALL');
  const [scopedCollectionId] = useState(searchParams.get('collectionId') || null);    
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [searchStats, setSearchStats] = useState(null);
  const [selectedResult, setSelectedResult] = useState(null);
  
  const debounceTimer = useRef(null);

  // --- 1. THE SEARCH CORE ---
  const performSearch = useCallback(async (searchQuery, filter) => {
    if (!searchQuery.trim()) {
      setResults([]);
      setSearchStats(null);
      return;
    }

    setLoading(true);
    try {
      const response = await api.post('/search/', {
        query: searchQuery,
        file_type: filter === 'ALL' ? null : filter,
        collection_id: scopedCollectionId,
        limit: 20
      });
      
      setResults(response.data.results);
      setSearchStats({
        count: response.data.total_count,
        time: response.data.search_time_ms
      });
    } catch (err) {
      toast.error("Search failed. Check your API key.");
    } finally {
      setLoading(false);
    }
  }, []);

  // --- 2. DEBOUNCE LOGIC (300ms) ---
  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);

    debounceTimer.current = setTimeout(() => {
      performSearch(query, activeFilter);
    }, 300);

    return () => clearTimeout(debounceTimer.current);
  }, [query, activeFilter, performSearch]);

  // --- 3. VOICE SEARCH (Web Speech API) ---
  const handleVoiceSearch = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    
    if (!SpeechRecognition) {
      toast.error("Speech recognition not supported in this browser.");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'en-US';

    recognition.onstart = () => setIsListening(true);
    recognition.onend = () => setIsListening(false);
    
    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      setQuery(transcript);
      toast.success(`Heard: "${transcript}"`, { icon: '🎤' });
    };

    recognition.start();
  };

  // --- 4. SKELETON COMPONENTS ---
  const SkeletonCard = () => (
    <div className="bg-white rounded-[32px] border border-slate-100 p-4 space-y-4 animate-pulse">
      <div className="aspect-[4/3] bg-slate-100 rounded-2xl" />
      <div className="h-4 bg-slate-100 rounded-full w-2/3" />
      <div className="h-3 bg-slate-50 rounded-full w-1/2" />
    </div>
  );

  return (
    <div className="min-h-screen bg-[#F8FAFC]">
      
      {/* HEADER & SEARCH BAR */}
      <header className="bg-white/80 backdrop-blur-xl border-b border-slate-200 sticky top-0 z-50 py-6 px-8">
        <div className="max-w-5xl mx-auto space-y-6">
          
          <div className="relative group">
            <div className="absolute left-6 top-1/2 -translate-y-1/2 text-slate-400 group-focus-within:text-brand transition-colors">
              {loading ? <Loader2 className="w-6 h-6 animate-spin" /> : <SearchIcon className="w-6 h-6" />}
            </div>
            
            <input 
              autoFocus
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search images, videos, audio or documents in plain English..."
              className="w-full pl-16 pr-20 py-6 bg-slate-50 border-2 border-transparent rounded-[32px] outline-none text-lg font-bold text-slate-800 placeholder:text-slate-400 focus:bg-white focus:border-brand focus:shadow-2xl focus:shadow-brand/10 transition-all"
            />

            <button 
              onClick={handleVoiceSearch}
              className={`absolute right-4 top-1/2 -translate-y-1/2 p-4 rounded-2xl transition-all cursor-pointer ${isListening ? 'bg-red-500 text-white animate-pulse' : 'bg-white text-slate-400 hover:text-brand shadow-sm'}`}
            >
              {isListening ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
            </button>
          </div>

          {/* FILTER CHIPS */}
          <div className="flex items-center gap-3 overflow-x-auto pb-2 scrollbar-hide">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setActiveFilter(f.id)}
                className={`flex items-center gap-2 px-6 py-2.5 rounded-full text-xs font-black uppercase tracking-widest border-2 transition-all whitespace-nowrap cursor-pointer ${activeFilter === f.id ? 'bg-slate-900 border-slate-900 text-white shadow-lg' : 'bg-white border-slate-100 text-slate-400 hover:border-slate-300'}`}
              >
                <f.icon className="w-3.5 h-3.5" />
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* RESULTS AREA */}
      <main className="max-w-7xl mx-auto px-8 py-12">
        
        {scopedCollectionId && (
            <div className="mb-6 px-4">
                <div className="inline-flex items-center gap-2 px-5 py-2.5 bg-brand/10 border border-brand/20 rounded-2xl">
                <span className="text-[10px] font-black text-brand uppercase tracking-widest">
                    Scoped to Collection
                </span>
                <button 
                    onClick={() => window.location.href = '/search'}
                    className="text-brand hover:text-slate-900 transition-colors"
                >
                    <X className="w-3 h-3" />
                </button>
                </div>
            </div>
            )}

        {/* STATS BAR */}

        {searchStats && !loading && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center justify-between mb-8 px-4">
            <p className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em]">
               Intelligence found <span className="text-slate-900">{searchStats.count}</span> matches in <span className="text-brand">{searchStats.time}ms</span>
            </p>
          </motion.div>
        )}

        {/* LOADING SKELETONS */}
        {loading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
            {[...Array(8)].map((_, i) => <SkeletonCard key={i} />)}
          </div>
        )}

        {/* DATA GRID */}
        {!loading && results.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
            {results.map((result) => (
              <SearchResultCard 
                key={result.media_item_id} 
                result={result} 
                onClick={(r) => setSelectedResult(r)}
              />
            ))}
          </div>
        )}

        {/* EMPTY STATES */}
        {!loading && query && results.length === 0 && (
          <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="text-center py-32 bg-white rounded-[60px] border-4 border-dashed border-slate-50">
             <div className="w-20 h-20 bg-slate-50 rounded-3xl flex items-center justify-center mx-auto mb-6 text-slate-200">
                <Sparkles className="w-10 h-10" />
             </div>
             <h3 className="text-2xl font-black text-slate-800">No semantic matches found</h3>
             <p className="text-slate-400 font-bold max-w-xs mx-auto mt-2">Try describing the content differently or check your spelling.</p>
          </motion.div>
        )}

        {!query && (
           <div className="text-center py-32 opacity-20">
              <History className="w-16 h-16 mx-auto mb-4" />
              <p className="font-black uppercase tracking-widest text-xs">Waiting for your query...</p>
           </div>
        )}
      </main>

      <AnimatePresence>
        {selectedResult && (
          <SearchResultModal
            result={selectedResult}
            onClose={() => setSelectedResult(null)}
            onFindSimilar={(filename) => {
              setSelectedResult(null);
              setQuery(filename);
            }}
          />
        )}
      </AnimatePresence>

    </div>
  );
};

export default Search;