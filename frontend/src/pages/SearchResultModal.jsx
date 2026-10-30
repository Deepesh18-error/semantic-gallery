import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  X, Calendar, HardDrive, Sparkles, 
  Play, FileText, Music, Image as ImageIcon, 
  ExternalLink, Search, Clock, Brain, Loader2,
  CheckCircle2, AlertCircle
} from 'lucide-react';
import api from '../services/api';

const SearchResultModal = ({ result, onClose, onFindSimilar }) => {
  const [fileUrl, setFileUrl] = useState(null);
  const [loading, setLoading] = useState(true);
  const [aiDescription, setAiDescription] = useState(null);
  const [descStatus, setDescStatus] = useState(null);
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

  // --- 2. FETCH AI DESCRIPTION FROM MONGODB (via embedding info endpoint) ---
  // We call the existing /media/info/ endpoint which returns ai_description
  // and description_status stored by description_service.py
  useEffect(() => {
    const fetchDescription = async () => {
      // Only fetch for visual media — documents already show chunk preview
      if (!['IMAGE', 'VIDEO', 'AUDIO'].includes(result.media_type)) return;

      try {
        const res = await api.get(`/media/info/${result.media_item_id}/`);
        if (res.data) {
          setAiDescription(res.data.ai_description || null);
          setDescStatus(res.data.description_status || null);
        }
      } catch (err) {
        // Non-critical — modal still works without description
        console.log("Could not fetch AI description:", err);
      }
    };

    fetchDescription();
  }, [result.media_item_id]);

  // --- 3. THE AUTO-SEEK ENGINE ---
  const handleLoadedMetadata = () => {
    if (mediaRef.current && result.matched_segment_start_time) {
      mediaRef.current.currentTime = result.matched_segment_start_time;
    }
  };

  // Close on Escape key
  useEffect(() => {
    const handleEsc = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  if (!result) return null;

  // Determines the color of the match badge
  const matchColor = result.similarity_percentage >= 80 
    ? 'bg-emerald-500' 
    : result.similarity_percentage >= 60 
    ? 'bg-amber-500' 
    : 'bg-slate-500';

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-end">
      
      {/* BACKDROP OVERLAY */}
      <motion.div 
        initial={{ opacity: 0 }} 
        animate={{ opacity: 1 }} 
        exit={{ opacity: 0 }}
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/60 backdrop-blur-md cursor-pointer"
      />

      {/* MODAL PANEL */}
      <motion.div 
        initial={{ x: '100%' }} 
        animate={{ x: 0 }} 
        exit={{ x: '100%' }}
        transition={{ type: 'spring', damping: 25, stiffness: 200 }}
        className="relative bg-white w-full max-w-2xl h-full shadow-2xl flex flex-col overflow-hidden"
      >
        
        {/* HEADER */}
        <div className="p-8 border-b border-slate-100 flex items-center justify-between shrink-0">
          <div className="flex-1 min-w-0 pr-4">
            <div className="flex items-center gap-3 mb-2">
              <span className="bg-slate-900 text-white px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest">
                {result.media_type}
              </span>
              <span className={`px-3 py-1 rounded-full text-white text-[10px] font-black uppercase tracking-widest ${matchColor}`}>
                {result.similarity_percentage}% SEMANTIC MATCH
              </span>
            </div>
            <h2 className="text-xl font-black text-slate-900 truncate">
              {result.original_filename}
            </h2>
          </div>
          <button 
            onClick={onClose} 
            className="p-3 hover:bg-slate-50 rounded-2xl transition-all cursor-pointer shrink-0"
          >
            <X className="w-6 h-6 text-slate-400" />
          </button>
        </div>

        {/* SCROLLABLE BODY */}
        <div className="flex-1 overflow-y-auto p-8 space-y-8">
          
          {/* ── MEDIA PREVIEW ── */}
          <div className="bg-slate-50 rounded-[32px] overflow-hidden border border-slate-100 relative">
            {loading ? (
              <div className="aspect-video flex items-center justify-center">
                <Loader2 className="animate-spin text-indigo-500 w-10 h-10" />
              </div>
            ) : (
              <>
                {result.media_type === 'IMAGE' && (
                  <img 
                    src={fileUrl} 
                    className="w-full h-auto object-contain max-h-[480px]" 
                    alt={result.original_filename} 
                  />
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
                    <div className="w-20 h-20 bg-indigo-500/20 rounded-full flex items-center justify-center animate-pulse">
                      <Music className="w-10 h-10 text-indigo-400" />
                    </div>
                    <audio 
                      ref={mediaRef}
                      controls 
                      className="w-full"
                      onLoadedMetadata={handleLoadedMetadata}
                    >
                      <source src={fileUrl} type="audio/mpeg" />
                    </audio>
                    <p className="text-[10px] font-black uppercase tracking-[0.3em] text-indigo-400">
                      Deep sonic jump enabled
                    </p>
                  </div>
                )}

                {result.media_type === 'DOCUMENT' && (
                  <div className="p-12 bg-white flex flex-col items-center gap-4">
                    <FileText className="w-16 h-16 text-slate-200" />
                    <p className="text-xs font-bold text-slate-400 uppercase tracking-widest">
                      Document Preview via Snippet
                    </p>
                  </div>
                )}
              </>
            )}
          </div>

          {/* ── AI UNDERSTANDING SECTION ── */}
          {/* Shown for IMAGE, VIDEO, AUDIO after description_service.py runs */}
          {['IMAGE', 'VIDEO', 'AUDIO'].includes(result.media_type) && (
            <div className="space-y-4">
              
              {/* Section Header */}
              <div className="flex items-center gap-3">
                <div className="p-2 bg-indigo-50 rounded-xl">
                  <Brain className="w-4 h-4 text-indigo-500" />
                </div>
                <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest">
                  AI Understanding
                </h3>
                {/* Status badge — shows current description_status */}
                {descStatus === 'DONE' && (
                  <span className="ml-auto flex items-center gap-1.5 text-[10px] font-black text-emerald-600 uppercase tracking-wider">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Indexed
                  </span>
                )}
                {descStatus === 'GENERATING' && (
                  <span className="ml-auto flex items-center gap-1.5 text-[10px] font-black text-amber-500 uppercase tracking-wider">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Generating...
                  </span>
                )}
                {descStatus === 'FAILED' && (
                  <span className="ml-auto flex items-center gap-1.5 text-[10px] font-black text-red-400 uppercase tracking-wider">
                    <AlertCircle className="w-3.5 h-3.5" />
                    Analysis Failed
                  </span>
                )}
              </div>

              {/* Description Card */}
              {aiDescription ? (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1 }}
                  className="relative bg-gradient-to-br from-indigo-50 to-slate-50 rounded-[28px] p-7 border border-indigo-100 overflow-hidden"
                >
                  {/* Decorative sparkle — top right corner */}
                  <Sparkles className="absolute top-5 right-5 w-5 h-5 text-indigo-200" />
                  
                  <p className="text-slate-600 leading-relaxed text-sm font-medium pr-6">
                    {aiDescription}
                  </p>
                  
                  {/* Footer label */}
                  <div className="mt-5 pt-4 border-t border-indigo-100/70 flex items-center gap-2">
                    <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                    <p className="text-[10px] font-black text-indigo-400 uppercase tracking-widest">
                      Generated by Gemini Flash · Used for semantic ranking
                    </p>
                  </div>
                </motion.div>
              ) : descStatus === 'GENERATING' ? (
                // Skeleton while generating
                <div className="bg-slate-50 rounded-[28px] p-7 border border-slate-100 space-y-3 animate-pulse">
                  <div className="h-3 bg-slate-200 rounded-full w-full" />
                  <div className="h-3 bg-slate-200 rounded-full w-5/6" />
                  <div className="h-3 bg-slate-200 rounded-full w-4/6" />
                  <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest pt-2">
                    Intelligence pipeline running...
                  </p>
                </div>
              ) : descStatus === 'FAILED' ? (
                // Failed state
                <div className="bg-red-50 rounded-[28px] p-7 border border-red-100">
                  <p className="text-sm text-red-400 font-medium">
                    AI analysis could not be completed for this file. 
                    Visual search still works via pixel embeddings.
                  </p>
                </div>
              ) : (
                // PENDING — description not yet generated (new upload, backfill not run yet)
                <div className="bg-slate-50 rounded-[28px] p-7 border border-slate-100">
                  <p className="text-sm text-slate-400 font-medium">
                    AI analysis queued. This file will be fully indexed shortly.
                  </p>
                </div>
              )}
            </div>
          )}

          {/* ── INTELLIGENCE SNIPPET (Documents only) ── */}
          {result.media_type === 'DOCUMENT' && result.matched_chunk_preview && (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-indigo-50 rounded-xl">
                  <Sparkles className="w-4 h-4 text-indigo-500" />
                </div>
                <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest">
                  Matching Intelligence
                </h3>
              </div>
              <div className="bg-slate-50 p-8 rounded-[28px] border-l-8 border-indigo-500 shadow-inner">
                <p className="text-slate-600 leading-relaxed font-medium italic text-sm">
                  "...{result.matched_chunk_preview}..."
                </p>
              </div>
            </div>
          )}

          {/* ── MULTIMEDIA TIMESTAMP JUMP (Video / Audio) ── */}
          {(result.media_type === 'VIDEO' || result.media_type === 'AUDIO') && 
           result.matched_segment_start_time !== undefined && (
            <div className="bg-slate-900 rounded-[28px] p-7 text-white flex items-center justify-between">
              <div>
                <h4 className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1">
                  Target Moment
                </h4>
                <p className="text-lg font-black">
                  Segment {Math.floor(result.matched_segment_start_time / 120) + 1}
                </p>
              </div>
              <div className="bg-indigo-500 text-white px-6 py-3 rounded-2xl flex items-center gap-3 font-black text-sm">
                <Clock className="w-4 h-4" />
                {Math.floor(result.matched_segment_start_time / 60)}:{Math.floor(result.matched_segment_start_time % 60).toString().padStart(2, '0')}
              </div>
            </div>
          )}

          {/* ── SYSTEM METADATA ── */}
          <div className="grid grid-cols-2 gap-4">
            <div className="p-6 bg-slate-50 rounded-3xl border border-slate-100 flex items-center gap-4">
              <Calendar className="text-slate-400 w-5 h-5 shrink-0" />
              <div className="min-w-0">
                <p className="text-[10px] font-black text-slate-400 uppercase tracking-wider">
                  Registered On
                </p>
                <p className="text-sm font-bold text-slate-700">
                  {new Date(result.created_at).toLocaleDateString()}
                </p>
              </div>
            </div>
            <div className="p-6 bg-slate-50 rounded-3xl border border-slate-100 flex items-center gap-4">
              <HardDrive className="text-slate-400 w-5 h-5 shrink-0" />
              <div className="min-w-0">
                <p className="text-[10px] font-black text-slate-400 uppercase tracking-wider">
                  File Volume
                </p>
                <p className="text-sm font-bold text-slate-700">
                  {(result.file_size_bytes / 1024 / 1024).toFixed(2)} MB
                </p>
              </div>
            </div>
          </div>

          {/* ── ACTIONS ── */}
          <div className="pt-4 flex gap-4">
            <button 
              onClick={() => onFindSimilar(result.original_filename)}
              className="flex-1 bg-slate-900 text-white py-5 rounded-3xl font-black flex items-center justify-center gap-3 hover:bg-indigo-600 transition-all shadow-xl active:scale-95 cursor-pointer text-sm"
            >
              <Search className="w-5 h-5" />
              Find Similar Results
            </button>
            <button 
              className="p-5 bg-slate-100 text-slate-400 rounded-3xl hover:bg-slate-200 transition-all cursor-pointer"
              onClick={() => window.open(fileUrl, '_blank')}
              title="Open in new tab"
            >
              <ExternalLink className="w-5 h-5" />
            </button>
          </div>

          {/* Bottom padding for scroll breathing room */}
          <div className="h-4" />
        </div>
      </motion.div>
    </div>
  );
};

export default SearchResultModal;