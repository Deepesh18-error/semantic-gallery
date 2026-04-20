import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  X, Calendar, HardDrive, Cpu, Sparkles, 
  Play, FileText, Music, Image as ImageIcon, 
  ExternalLink, Search, Clock
} from 'lucide-react';
import api from '../services/api';

const SearchResultModal = ({ result, onClose, onFindSimilar }) => {
  const [fileUrl, setFileUrl] = useState(null);
  const [loading, setLoading] = useState(true);
  const mediaRef = useRef(null);

  // --- 1. FETCH ACTUAL MEDIA FILE ---
  useEffect(() => {
    const fetchFile = async () => {
      try {
        const res = await api.get(`/media/file/${result.media_item_id}/`, { 
          responseType: 'blob' 
        });
        const url = URL.createObjectURL(res.data);
        setFileUrl(url);
      } catch (err) {
        console.error("Failed to load file", err);
      } finally {
        setLoading(false);
      }
    };

    fetchFile();
    return () => fileUrl && URL.revokeObjectURL(fileUrl);
  }, [result.media_item_id]);

  // --- 2. THE AUTO-SEEK ENGINE ---
  // When video/audio metadata loads, jump to the matching moment
  const handleLoadedMetadata = () => {
    if (mediaRef.current && result.matched_segment_start_time) {
      mediaRef.current.currentTime = result.matched_segment_start_time;
      // mediaRef.current.play(); // Optional: Auto-play the moment
    }
  };

  // Close on Escape key
  useEffect(() => {
    const handleEsc = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  if (!result) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-end">
      {/* OVERLAY */}
      <motion.div 
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/60 backdrop-blur-md cursor-pointer"
      />

      {/* MODAL PANEL */}
      <motion.div 
        initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
        transition={{ type: 'spring', damping: 25, stiffness: 200 }}
        className="relative bg-white w-full max-w-2xl h-full shadow-2xl flex flex-col overflow-hidden"
      >
        
        {/* HEADER SECTION */}
        <div className="p-8 border-b border-slate-100 flex items-center justify-between">
          <div className="flex-1 min-w-0 pr-4">
            <div className="flex items-center gap-3 mb-1">
               <span className="bg-slate-900 text-white px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest">
                  {result.media_type}
               </span>
               <span className={`px-3 py-1 rounded-full text-white text-[10px] font-black uppercase tracking-widest ${result.similarity_percentage >= 80 ? 'bg-emerald-500' : 'bg-amber-500'}`}>
                  {result.similarity_percentage}% SEMANTIC MATCH
               </span>
            </div>
            <h2 className="text-2xl font-black text-slate-900 truncate">
               {result.original_filename}
            </h2>
          </div>
          <button onClick={onClose} className="p-3 hover:bg-slate-50 rounded-2xl transition-all cursor-pointer">
            <X className="w-6 h-6 text-slate-400" />
          </button>
        </div>

        {/* MAIN BODY (Scrollable) */}
        <div className="flex-1 overflow-y-auto p-8 space-y-10">
          
          {/* PREVIEW CONTAINER */}
          <div className="bg-slate-50 rounded-[40px] overflow-hidden border border-slate-100 relative group">
            {loading ? (
              <div className="aspect-video flex items-center justify-center">
                 <Loader2 className="animate-spin text-brand w-10 h-10" />
              </div>
            ) : (
              <>
                {result.media_type === 'IMAGE' && (
                  <img src={fileUrl} className="w-full h-auto object-contain max-h-[500px]" alt="Full View" />
                )}

                {result.media_type === 'VIDEO' && (
                  <video 
                    ref={mediaRef}
                    controls 
                    className="w-full aspect-video bg-black"
                    onLoadedMetadata={handleLoadedMetadata}
                  >
                    <source src={fileUrl} type="video/mp4" />
                  </video>
                )}

                {result.media_type === 'AUDIO' && (
                  <div className="p-12 flex flex-col items-center gap-8 bg-slate-900 text-white">
                    <div className="w-20 h-20 bg-brand/20 rounded-full flex items-center justify-center animate-pulse">
                        <Music className="w-10 h-10 text-brand" />
                    </div>
                    <audio 
                      ref={mediaRef}
                      controls 
                      className="w-full"
                      onLoadedMetadata={handleLoadedMetadata}
                    >
                      <source src={fileUrl} type="audio/mpeg" />
                    </audio>
                    <p className="text-[10px] font-black uppercase tracking-[0.3em] text-brand">Deep sonic jump enabled</p>
                  </div>
                )}

                {result.media_type === 'DOCUMENT' && (
                  <div className="p-12 bg-white flex flex-col items-center gap-6">
                     <FileText className="w-16 h-16 text-slate-200" />
                     <p className="text-xs font-bold text-slate-400 uppercase tracking-widest">Document View Restricted to Snippets</p>
                  </div>
                )}
              </>
            )}
          </div>

          {/* INTELLIGENCE SNIPPET (For Documents) */}
          {result.media_type === 'DOCUMENT' && result.matched_chunk_preview && (
            <div className="space-y-4">
               <div className="flex items-center gap-3">
                  <Sparkles className="w-5 h-5 text-brand" />
                  <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest">Matching Intelligence</h3>
               </div>
               <div className="bg-slate-50 p-8 rounded-[40px] border-l-8 border-brand shadow-inner">
                  <p className="text-slate-600 leading-relaxed font-medium italic">
                    "...{result.matched_chunk_preview}..."
                  </p>
               </div>
            </div>
          )}

          {/* MULTIMEDIA TIMESTAMP JUMP (For Video/Audio) */}
          {(result.media_type === 'VIDEO' || result.media_type === 'AUDIO') && (
            <div className="bg-slate-900 rounded-[40px] p-8 text-white flex items-center justify-between">
               <div>
                  <h4 className="text-xs font-black uppercase tracking-widest text-slate-500 mb-1">Target Moment</h4>
                  <p className="text-xl font-black">Segment {Math.floor(result.matched_segment_start_time / 120) + 1}</p>
               </div>
               <div className="bg-brand text-white px-6 py-3 rounded-2xl flex items-center gap-3 font-black text-sm">
                  <Clock className="w-5 h-5" />
                  Starts at {Math.floor(result.matched_segment_start_time / 60)}:{Math.floor(result.matched_segment_start_time % 60).toString().padStart(2, '0')}
               </div>
            </div>
          )}

          {/* SYSTEM METADATA */}
          <div className="grid grid-cols-2 gap-4">
             <div className="p-6 bg-slate-50 rounded-3xl border border-slate-100 flex items-center gap-4">
                <Calendar className="text-slate-400 w-5 h-5" />
                <div>
                   <p className="text-[10px] font-black text-slate-400 uppercase">Registered On</p>
                   <p className="text-sm font-bold text-slate-700">{new Date(result.created_at).toLocaleDateString()}</p>
                </div>
             </div>
             <div className="p-6 bg-slate-50 rounded-3xl border border-slate-100 flex items-center gap-4">
                <HardDrive className="text-slate-400 w-5 h-5" />
                <div>
                   <p className="text-[10px] font-black text-slate-400 uppercase">File Volume</p>
                   <p className="text-sm font-bold text-slate-700">{(result.file_size_bytes / 1024 / 1024).toFixed(2)} MB</p>
                </div>
             </div>
          </div>

          {/* ACTIONS */}
          <div className="pt-10 flex gap-4">
             <button 
               onClick={() => onFindSimilar(result.original_filename)}
               className="flex-1 bg-slate-900 text-white py-5 rounded-3xl font-black flex items-center justify-center gap-3 hover:bg-brand transition-all shadow-xl active:scale-95 cursor-pointer"
             >
                <Search className="w-5 h-5" />
                Find Similar Results
             </button>
             <button 
               className="p-5 bg-slate-100 text-slate-400 rounded-3xl hover:bg-slate-200 transition-all cursor-pointer"
               onClick={() => window.open(fileUrl, '_blank')}
             >
                <ExternalLink className="w-6 h-6" />
             </button>
          </div>
        </div>
      </motion.div>
    </div>
  );
};

export default SearchResultModal;