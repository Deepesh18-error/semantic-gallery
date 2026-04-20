import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { 
  Image as ImageIcon, Video, Music, FileText, 
  Play, Clock, Database, ChevronRight 
} from 'lucide-react';
import api from '../services/api';

// --- HELPER: Seconds to MM:SS ---
const formatTime = (seconds) => {
  if (!seconds) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
};

const SearchResultCard = ({ result, onClick }) => {
  const [thumbUrl, setThumbUrl] = useState(null);
  const [loading, setLoading] = useState(true);

  // --- 1. FETCH PREVIEW (WITH AUTH) ---
  useEffect(() => {
    // Audio and Plain Text don't have visual thumbnails
    if (result.media_type === 'AUDIO' || result.media_type === 'TEXT') {
      setLoading(false);
      return;
    }

    let objectUrl = null;
    const fetchThumb = async () => {
      try {
        const url = result.media_type === 'IMAGE' 
          ? `/media/file/${result.media_item_id}/` 
          : `/media/file/${result.media_item_id}/?thumbnail=true`;

        const res = await api.get(url, { responseType: 'blob' });
        objectUrl = URL.createObjectURL(res.data);
        setThumbUrl(objectUrl);
      } catch (err) {
        console.warn("Thumb fetch failed", err);
      } finally {
        setLoading(false);
      }
    };

    fetchThumb();
    return () => objectUrl && URL.revokeObjectURL(objectUrl);
  }, [result.media_item_id, result.media_type]);

  // --- 2. DYNAMIC BADGE COLORING ---
  const getScoreColor = (score) => {
    if (score >= 80) return 'bg-emerald-500';
    if (score >= 60) return 'bg-amber-500';
    return 'bg-orange-500';
  };

  return (
    <motion.div
      whileHover={{ y: -5 }}
      onClick={() => onClick(result)}
      className="group bg-white rounded-[40px] border border-slate-100 overflow-hidden shadow-sm hover:shadow-2xl hover:border-brand/20 transition-all cursor-pointer flex flex-col"
    >
      {/* THUMBNAIL AREA */}
      <div className="relative aspect-[4/3] bg-slate-50 flex items-center justify-center overflow-hidden">
        
        {/* Main Preview Content */}
        {loading ? (
          <div className="w-full h-full animate-pulse bg-slate-100" />
        ) : thumbUrl ? (
          <img 
            src={thumbUrl} 
            alt="Result Preview" 
            className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-700" 
          />
        ) : (
          <div className="flex flex-col items-center gap-3 opacity-20">
            {result.media_type === 'AUDIO' && <Music className="w-12 h-12 text-violet-500" />}
            {result.media_type === 'VIDEO' && <Video className="w-12 h-12 text-emerald-500" />}
            {result.media_type === 'DOCUMENT' && <FileText className="w-12 h-12 text-blue-500" />}
            <span className="text-[10px] font-black uppercase tracking-widest">{result.media_type}</span>
          </div>
        )}

        {/* Similarity Badge (Top Right) */}
        <div className={`absolute top-4 right-4 z-10 px-3 py-1.5 rounded-2xl text-white text-[10px] font-black shadow-lg ${getScoreColor(result.similarity_percentage)}`}>
           {result.similarity_percentage}% MATCH
        </div>

        {/* Play Icon Overlay (For Videos) */}
        {result.media_type === 'VIDEO' && (
          <div className="absolute inset-0 bg-slate-900/20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="bg-white/90 p-4 rounded-full shadow-xl text-brand">
              <Play className="w-6 h-6 fill-brand" />
            </div>
          </div>
        )}

        {/* Deep Link Badge (Bottom Left - Video/Audio) */}
        {(result.media_type === 'VIDEO' || result.media_type === 'AUDIO') && (
          <div className="absolute bottom-4 left-4 bg-slate-900/80 backdrop-blur-md text-white px-3 py-1.5 rounded-xl flex items-center gap-2 text-[10px] font-black">
             <Clock className="w-3 h-3 text-brand" />
             Match at {formatTime(result.matched_segment_start_time)}
          </div>
        )}
      </div>

      {/* METADATA AREA */}
      <div className="p-6 flex-1 flex flex-col">
        <div className="flex-1 space-y-3">
          <p className="text-sm font-black text-slate-800 line-clamp-1 group-hover:text-brand transition-colors">
            {result.original_filename}
          </p>

          {/* Document Intelligence (Snippet Preview) */}
          {result.media_type === 'DOCUMENT' && result.matched_chunk_preview && (
            <div className="bg-slate-50 p-4 rounded-2xl border border-slate-100 italic text-slate-500 text-xs leading-relaxed line-clamp-3">
               "{result.matched_chunk_preview}..."
            </div>
          )}

          {/* Audio/Video Duration Placeholder */}
          {result.media_type === 'AUDIO' && (
            <div className="flex items-center gap-2 text-slate-400">
               <Music className="w-3.5 h-3.5" />
               <span className="text-[10px] font-bold uppercase tracking-widest">Deep Sonic Match</span>
            </div>
          )}
        </div>

        {/* Footer info */}
        <div className="mt-6 pt-4 border-t border-slate-50 flex items-center justify-between">
           <div className="flex items-center gap-2 text-[10px] font-black text-slate-300 uppercase tracking-tighter">
              <Database className="w-3 h-3" />
              Vault Room: {result.collection_id.substring(0,8)}
           </div>
           <div className="text-[10px] font-black text-slate-400">
              {(result.file_size_bytes / 1024 / 1024).toFixed(1)} MB
           </div>
        </div>
      </div>
    </motion.div>
  );
};

export default SearchResultCard;