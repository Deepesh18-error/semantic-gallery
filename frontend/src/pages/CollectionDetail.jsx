import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useDropzone } from 'react-dropzone';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  ArrowLeft, Upload, File, Image as ImageIcon, Video, 
  Music, Loader2, Trash2, Clock, HardDrive, Play, FileText, MousePointer2, Check, XCircle, RotateCcw, AlertCircle
} from 'lucide-react';
import api from '../services/api';
import toast from 'react-hot-toast';


const SmartMediaPreview = ({ mediaId, mediaType, processingStatus }) => {
  const [imgUrl, setImgUrl] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Don't attempt thumbnail fetch until the AI pipeline is DONE
    // This is the key fix — no point hitting the server before the thumbnail exists
    if (processingStatus !== 'EMBEDDED') {
      setLoading(false);
      return;
    }

    let objectUrl = null;

    const fetchPreview = async () => {
      try {
        const response = await api.get(`/media/file/${mediaId}/?thumbnail=true`, { 
          responseType: 'blob' 
        });
        objectUrl = URL.createObjectURL(response.data);
        setImgUrl(objectUrl);
      } catch (err) { 
        console.warn(`Preview not ready for ${mediaId}`); 
      } finally { 
        setLoading(false); 
      }
    };

    fetchPreview();

    // Cleanup the blob URL when the component unmounts to avoid memory leaks
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [mediaId, processingStatus]); // Re-run when status changes to EMBEDDED

  if (loading) return (
    <div className="w-full h-full bg-slate-50 animate-pulse flex items-center justify-center">
      <Loader2 className="animate-spin text-slate-200 w-8 h-8" />
    </div>
  );
  
  if (!imgUrl) return (
    <div className="flex flex-col items-center gap-3 text-slate-300">
      {mediaType === 'VIDEO' 
        ? <Video className="w-16 h-16 opacity-30" /> 
        : <ImageIcon className="w-16 h-16 opacity-30" />
      }
      <span className="text-[9px] font-black uppercase tracking-widest bg-slate-100 px-3 py-1 rounded-full text-slate-400">
        No Preview
      </span>
    </div>
  );

  return (
    <img 
      src={imgUrl} 
      alt="Vault Media" 
      className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-700" 
    />
  );
};



  const ShimmerOverlay = () => (
    <motion.div
      initial={{ x: '-100%' }}
      animate={{ x: '100%' }}
      transition={{ repeat: Infinity, duration: 1.5, ease: 'linear' }}
      className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent z-10"
    />
  );


const MediaDetailModal = ({ item, onClose }) => {
  if (!item) return null;

  return (
    <motion.div 
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 z-[100] flex items-center justify-end p-6 bg-slate-900/60 backdrop-blur-md"
      onClick={onClose}
    >
      <motion.div 
        initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
        className="bg-white h-full w-full max-w-2xl rounded-[40px] shadow-2xl overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()} // Prevent closing when clicking inside
      >
        {/* MODAL HEADER */}
        <div className="p-8 border-b border-slate-100 flex justify-between items-center">
          <div>
            <h2 className="text-2xl font-black text-slate-900 truncate max-w-md">{item.file_metadata.original_name}</h2>
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mt-1">Deep Index Intelligence Report</p>
          </div>
          <button onClick={onClose} className="p-3 hover:bg-slate-100 rounded-2xl transition-all"><XCircle /></button>
        </div>

        {/* MODAL CONTENT */}
        <div className="flex-1 overflow-y-auto p-8">
          
          {/* 1. DOCUMENT CHUNK BREAKDOWN */}
          {item.media_type === 'DOCUMENT' && (
            <div className="space-y-6">
              <div className="flex items-center gap-3 mb-8">
                <FileText className="text-brand w-8 h-8" />
                <h3 className="font-black text-slate-800">Sliding Window Chunks ({item.total_vectors})</h3>
              </div>
              <div className="grid gap-3">
                {[...Array(item.total_vectors)].map((_, i) => (
                  <div key={i} className="group p-5 bg-slate-50 border border-slate-100 rounded-3xl hover:border-brand transition-all">
                    <div className="flex justify-between items-center">
                      <span className="text-xs font-black text-slate-400 uppercase">Vector #{i + 1}</span>
                      <span className="bg-white px-3 py-1 rounded-full text-[10px] font-bold text-slate-400 border border-slate-100">500 Tokens</span>
                    </div>
                    <p className="text-sm text-slate-500 mt-3 italic line-clamp-2">"This is a placeholder for chunk text preview. When we implement search, we will see the actual content that matched here..."</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 2. VIDEO/AUDIO TIMELINE VISUALIZATION */}
          {(item.media_type === 'VIDEO' || item.media_type === 'AUDIO') && (
            <div className="space-y-10">
              <div className="flex items-center gap-3">
                {item.media_type === 'VIDEO' ? <Video className="text-brand w-8 h-8" /> : <Music className="text-brand w-8 h-8" />}
                <h3 className="font-black text-slate-800">Temporal Index Breakdown</h3>
              </div>
              
              {/* THE TIMELINE BAR */}
              <div className="relative pt-10 pb-20">
                <div className="h-4 w-full bg-slate-100 rounded-full relative">
                  {[...Array(item.total_vectors)].map((_, i) => {
                    const width = 100 / item.total_vectors;
                    return (
                      <div 
                        key={i} 
                        className="absolute h-full border-r-2 border-white hover:bg-brand transition-all cursor-help group"
                        style={{ left: `${i * (width - 2)}%`, width: `${width}%`, backgroundColor: i % 2 === 0 ? '#3b82f6' : '#60a5fa' }}
                      >
                        {/* Segment Hover Label */}
                        <div className="absolute -bottom-12 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-all pointer-events-none whitespace-nowrap bg-slate-900 text-white text-[9px] px-3 py-1.5 rounded-lg shadow-xl font-black">
                           SEGMENT {i+1} • {i*120}s - {(i+1)*120}s
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="flex justify-between mt-4 text-[10px] font-black text-slate-300 uppercase tracking-widest">
                   <span>00:00 Start</span>
                   <span>End of Media</span>
                </div>
              </div>

              <div className="bg-slate-50 p-8 rounded-[40px] border border-slate-100">
                <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-4">Why is this split?</h4>
                <p className="text-sm text-slate-500 leading-relaxed font-medium">
                  To ensure maximum semantic accuracy, our AI slices your media into 120-second segments with a 10-second overlap. This prevents context loss at the edges of clips, making even the shortest mention searchable.
                </p>
              </div>
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
};


const CollectionDetail = () => {
  const { id: collectionId } = useParams();
  const navigate = useNavigate();

  // --- STATE SYSTEM ---
  const [collection, setCollection] = useState(null);
  const [mediaItems, setMediaItems] = useState([]);
  const [uploadQueue, setUploadQueue] = useState([]); // Tracks byte progress (Upload Progress)
  const [pendingIds, setPendingIds] = useState([]);   // Tracks AI progress (Polling Queue)
  const [loading, setLoading] = useState(true);
  const [selectedItem, setSelectedItem] = useState(null);

  const pollingInterval = useRef(null); // Ref to manage the timer cleanly

  // --- 1. INITIAL DATA FETCH & BOOTSTRAP POLLING ---
  useEffect(() => {
    const fetchVaultData = async () => {
      try {
        setLoading(true);
        // Fetch Collection Info
        const colRes = await api.get('/collections/');
        const found = colRes.data.find(c => c._id === collectionId);
        if (!found) return navigate('/');
        setCollection(found);

        // Fetch Items (Using the collections endpoint for now as per your code)
        // In the future, this should be: api.get(`/collections/${collectionId}/media/`)
        const mediaRes = await api.get('/collections/'); 
        const items = mediaRes.data; // Filter these as needed based on your DB structure
        setMediaItems(items);

        // 🧠 BOOTSTRAP: If any items are already PENDING in DB, add them to polling queue
        const unfinished = items
          .filter(i => i.processing_status === 'PENDING' || i.processing_status === 'PROCESSING')
          .map(i => i._id);
        
        if (unfinished.length > 0) {
            setPendingIds(unfinished);
        }

      } catch (err) {
        toast.error("Vault access denied.");
      } finally {
        setLoading(false);
      }
    };
    fetchVaultData();
  }, [collectionId, navigate]);


  // --- 2. THE HEARTBEAT ENGINE (Phase 3 Section 1) ---
  useEffect(() => {
    // If no items are pending, kill the interval and stop
    if (pendingIds.length === 0) {
      if (pollingInterval.current) clearInterval(pollingInterval.current);
      return;
    }

    // Start Polling every 3 seconds
    pollingInterval.current = setInterval(async () => {
      try {
        const idsParam = pendingIds.join(',');
        const response = await api.get(`/media/status/?ids=${idsParam}`);
        const statusUpdates = response.data; // Array of {_id, processing_status, error_message}

        // A. Update the UI Cards with new statuses
        setMediaItems(prevItems => prevItems.map(item => {
          const update = statusUpdates.find(u => u._id === item._id);
          return update ? { ...item, ...update } : item;
        }));

        // B. Update the Polling Queue (remove items that are no longer pending)
        const stillPending = statusUpdates
          .filter(u => u.processing_status === 'PENDING' || u.processing_status === 'PROCESSING')
          .map(u => u._id);
        
        setPendingIds(stillPending);

        // C. Success Notification
        const justFinished = statusUpdates.filter(u => u.processing_status === 'EMBEDDED');
        if (justFinished.length > 0) {
            toast.success(`${justFinished.length} items fully indexed! 🧠`, { id: 'status-update' });
        }
      } catch (err) {
        console.error("Heartbeat Check Failed", err);
      }
    }, 3000);

    // Cleanup when component closes or pendingIds changes
    return () => clearInterval(pollingInterval.current);
  }, [pendingIds]);

  // --- BULK RETRY LOGIC (Section 5) ---
  const handleBulkRetry = async () => {
    const failedItems = mediaItems.filter(i => i.processing_status === 'FAILED');
    if (failedItems.length === 0) return;

    const failedIds = failedItems.map(i => i._id);
    
    toast.promise(
      Promise.all(failedIds.map(id => api.post(`/media/retry/${id}/`))),
      {
        loading: `Re-submitting ${failedIds.length} items...`,
        success: "Batch re-submission successful! 🚀",
        error: "Some items failed to re-submit.",
      }
    );

    // 1. Update UI state for all failed items to PENDING locally
    setMediaItems(prev => prev.map(item => 
      failedIds.includes(item._id) ? { ...item, processing_status: 'PENDING', error_message: null } : item
    ));

    // 2. Re-inject into the Heartbeat Queue (Sets unique IDs to avoid duplicates)
    setPendingIds(prev => [...new Set([...prev, ...failedIds])]);
  };


  // --- 3. UPLOAD LOGIC (Multipart Truck + Polling Trigger) ---
  const onDrop = useCallback((acceptedFiles) => {
    acceptedFiles.forEach(async (file) => {
      const tempId = Math.random().toString(36).substring(7);
      
      // Step 1: Add to Visual Queue (Progress Bar)
      setUploadQueue(prev => [...prev, { id: tempId, name: file.name, progress: 0 }]);

      const formData = new FormData();
      formData.append('file', file);
      formData.append('collection_id', collectionId);

      try {
        const res = await api.post('/media/upload/', formData, {
          onUploadProgress: (p) => {
            const percent = Math.round((p.loaded * 100) / p.total);
            setUploadQueue(q => q.map(i => i.id === tempId ? { ...i, progress: percent } : i));
          }
        });
        
        // Step 2: Upload Complete! Remove from Queue and Add to Gallery
        const newItem = res.data;
        setMediaItems(prev => [newItem, ...prev]);
        setUploadQueue(q => q.filter(i => i.id !== tempId));

        // Step 3: TRIGGER THE HEARTBEAT (Add to polling queue)
        setPendingIds(prev => [...prev, newItem._id]);

        toast.success(`Ingested: ${file.name}`);
      } catch (err) {
        toast.error(`Failed: ${file.name}`);
        setUploadQueue(q => q.filter(i => i.id !== tempId));
      }
    });
  }, [collectionId]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop });

  // --- 4. THE ERASER ---
  const handleDelete = async (mediaId) => {
    const originalItems = [...mediaItems];
    setMediaItems(prev => prev.filter(i => i._id !== mediaId));

    try {
      await api.delete(`/media/delete/${mediaId}/`);
      toast.success("Item erased from vault.");
    } catch (err) {
      toast.error("Erase failed.");
      setMediaItems(originalItems);
    }
  };


  const handleRetry = async (mediaId) => {
    try {
      await api.post(`/media/retry/${mediaId}/`);
      
      // 1. Update local state to PENDING immediately
      setMediaItems(prev => prev.map(item => 
        item._id === mediaId ? { ...item, processing_status: 'PENDING', error_message: null } : item
      ));

      // 2. Re-add to polling queue to resume heartbeat
      setPendingIds(prev => [...new Set([...prev, mediaId])]);
      
      toast.success("Re-submitting to the AI Vault...");
    } catch (err) {
      toast.error("Retry failed. System offline.");
    }
  };


  if (loading) return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <Loader2 className="animate-spin text-brand w-12 h-12" />
    </div>
  );

  return (
    <div className="min-h-screen bg-[#F8FAFC]">
      
      {/* 1. HEADER SYSTEM */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-50 px-8 py-5 flex items-center justify-between">
        <div className="flex items-center gap-5">
          <Link to="/" className="p-2.5 hover:bg-slate-50 rounded-2xl border border-transparent hover:border-slate-100 transition-all">
            <ArrowLeft className="text-slate-400" />
          </Link>
          <div>
            <h1 className="text-2xl font-black text-slate-900 tracking-tight">{collection?.name}</h1>
            <p className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em]">
                {mediaItems.length} Registered Items
            </p>
          </div>
        </div>
        
        {/* Statistics Bar */}
        <div className="hidden lg:flex items-center gap-6 bg-slate-50 px-6 py-2.5 rounded-2xl border border-slate-100">
            <div className="flex gap-4 items-center">
                <ImageIcon className="w-4 h-4 text-blue-400" />
                <Video className="w-4 h-4 text-emerald-400" />
                <Music className="w-4 h-4 text-violet-400" />
                <FileText className="w-4 h-4 text-amber-400" />
            </div>
            <div className="w-[1px] h-6 bg-slate-200" />
            <div className="w-8 h-8 rounded-full border-4" style={{ borderColor: collection?.theme_color }} />
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-8 py-12">
        
        {/* 2. INGESTION ENGINE (Dropzone) */}
        <div {...getRootProps()} className={`
          border-4 border-dashed rounded-[48px] p-16 text-center transition-all cursor-pointer mb-16
          ${isDragActive ? 'bg-brand/5 border-brand scale-[0.99] shadow-2xl shadow-brand/10' : 'bg-white border-slate-100 hover:border-slate-200 shadow-sm'}
        `} style={{ borderColor: isDragActive ? collection?.theme_color : undefined }}>
          <input {...getInputProps()} />
          <div className={`w-20 h-20 mx-auto rounded-3xl flex items-center justify-center mb-6 transition-colors ${isDragActive ? 'bg-brand text-white shadow-lg' : 'bg-slate-50 text-slate-300'}`}>
            {isDragActive ? <MousePointer2 className="w-10 h-10" /> : <Upload className="w-10 h-10" />}
          </div>
          <h2 className="text-2xl font-black text-slate-800 mb-2">Drag media into the Vault</h2>
          <p className="text-slate-400 font-bold uppercase tracking-widest text-[10px]">Images • Video • Audio • PDF • Documents</p>
        </div>

        {/* 3. THE TRUCK (Upload Queue for Bytes Progress) */}
        <AnimatePresence>
          {uploadQueue.length > 0 && (
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mb-12 grid grid-cols-1 md:grid-cols-2 gap-4">
              {uploadQueue.map(item => (
                <div key={item.id} className="bg-white p-5 rounded-3xl border border-brand/20 flex items-center gap-4 shadow-lg">
                  <div className="bg-brand/10 p-3 rounded-2xl text-brand"><Loader2 className="animate-spin w-5 h-5" /></div>
                  <div className="flex-1">
                      <p className="text-sm font-black text-slate-700 truncate mb-2">{item.name}</p>
                      <div className="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
                        <div className="bg-brand h-full transition-all duration-300" style={{ width: `${item.progress}%` }} />
                      </div>
                  </div>
                </div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {/* 4. INTELLIGENCE-READY GRID (The Gallery) */}
        {mediaItems.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
            {mediaItems.map((item) => {
              const status = item.processing_status || 'PENDING';
              const isEmbedded = status === 'EMBEDDED';
              const isProcessing = status === 'PROCESSING';
              const isFailed = status === 'FAILED';

              return (
                <motion.div 
                  layout
                  key={item._id} 
                  // Trigger the Detail Modal only when index is complete
                  onClick={() => isEmbedded && setSelectedItem(item)}
                  initial={false}
                  animate={{
                    opacity: isEmbedded ? 1 : isFailed ? 0.9 : 0.7,
                    scale: isEmbedded ? [1, 1.05, 1] : 1,
                  }}
                  transition={{ duration: 0.5 }}
                  className={`
                    group bg-white rounded-[40px] border border-slate-100 overflow-hidden relative
                    transition-all duration-500
                    ${isEmbedded ? 'hover:shadow-2xl cursor-pointer' : 'pointer-events-none'}
                    ${isFailed ? 'border-red-100' : ''}
                  `}
                >
                  {/* PREVIEW AREA */}
                  <div className={`aspect-[4/5] bg-slate-50 relative overflow-hidden flex items-center justify-center ${!isEmbedded ? 'grayscale' : ''}`}>
                    
                    { (item.media_type === 'IMAGE' || item.media_type === 'VIDEO') ? (
                      <SmartMediaPreview mediaId={item._id} mediaType={item.media_type} processingStatus={item.processing_status}  />
                    ) : (
                      <div className="flex flex-col items-center gap-3 text-slate-300">
                        <FileText className="w-16 h-16 opacity-30" />
                        <span className="text-[10px] font-black uppercase tracking-widest bg-slate-100 px-3 py-1 rounded-full text-slate-400">
                          {item.media_type}
                        </span>
                      </div>
                    )}

                    {/* SHIMMER EFFECT (Section 2) */}
                    {isProcessing && <ShimmerOverlay />}

                    {/* STATUS BADGE OVERLAYS (Section 1 & 2) */}
                    <div className="absolute top-4 left-4 z-20">
                      {status === 'PENDING' && (
                        <div className="bg-slate-500/90 backdrop-blur-md text-white px-3 py-1.5 rounded-2xl flex items-center gap-2 text-[10px] font-black uppercase tracking-widest shadow-lg">
                          <Clock className="w-3.5 h-3.5" /> Queued
                        </div>
                      )}
                      {isProcessing && (
                        <div className="bg-brand backdrop-blur-md text-white px-3 py-1.5 rounded-2xl flex items-center gap-2 text-[10px] font-black uppercase tracking-widest shadow-lg animate-pulse">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Analyzing...
                        </div>
                      )}
                      {isEmbedded && (
                        <div className="bg-emerald-500 text-white px-3 py-1.5 rounded-2xl flex items-center gap-2 text-[10px] font-black uppercase tracking-widest shadow-lg">
                          <Check className="w-3.5 h-3.5" /> Ready
                        </div>
                      )}
                      {isFailed && (
                        <div className="bg-red-500 text-white px-3 py-1.5 rounded-2xl flex items-center gap-2 text-[10px] font-black uppercase tracking-widest shadow-lg">
                          <XCircle className="w-3.5 h-3.5" /> Failed
                        </div>
                      )}
                    </div>

                    {/* RETRY BUTTON (Section 2) */}
                    {isFailed && (
                      <div className="absolute inset-0 bg-red-50/60 backdrop-blur-[1px] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity pointer-events-auto">
                        <button 
                          onClick={(e) => { e.stopPropagation(); handleRetry(item._id); }}
                          className="bg-white text-red-500 px-6 py-3 rounded-2xl font-black text-xs flex items-center gap-2 shadow-xl hover:bg-red-500 hover:text-white transition-all transform active:scale-95 cursor-pointer"
                        >
                          <RotateCcw className="w-4 h-4" /> Retry AI Index
                        </button>
                      </div>
                    )}
                  </div>

                  {/* FOOTER INFO */}
                  <div className="p-6">
                    <div className="flex flex-col gap-4">
                      <div className="flex justify-between items-start">
                        <div className="max-w-[80%]">
                          <p className={`text-sm font-black truncate ${isEmbedded ? 'text-slate-800' : 'text-slate-400'}`}>
                            {item.file_metadata?.original_name || "Unknown File"}
                          </p>
                          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-tighter mt-1">
                            {((item.file_metadata?.size_bytes || 0) / 1024 / 1024).toFixed(1)} MB • {item.media_type}
                          </p>
                        </div>
                        
                        <button 
                          onClick={(e) => { e.stopPropagation(); isEmbedded && handleDelete(item._id); }} 
                          className={`p-3 rounded-2xl transition-all ${isEmbedded ? 'text-slate-300 hover:text-red-500 hover:bg-red-50' : 'text-slate-100 pointer-events-none'}`}
                          disabled={!isEmbedded}
                        >
                          <Trash2 className="w-5 h-5" />
                        </button>
                      </div>

                      {/* 🧠 INTELLIGENCE CHIP (Section 3) */}
                      {isEmbedded && item.total_vectors > 0 && (
                        <div className="flex">
                          <div className="bg-slate-900 text-[9px] font-black text-white px-3 py-1.5 rounded-xl uppercase tracking-wider flex items-center gap-2 shadow-sm border border-white/10">
                            <div className="w-1.5 h-1.5 bg-brand rounded-full animate-pulse" />
                            {item.total_vectors} {item.media_type === 'DOCUMENT' ? 'Semantic Chunks' : 'AI Segments'}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </div>
        ) : (
          <div className="text-center py-32 bg-white rounded-[60px] border-4 border-dashed border-slate-50">
             <HardDrive className="w-20 h-20 text-slate-100 mx-auto mb-6" />
             <h3 className="text-xl font-black text-slate-800">Vault Room is Empty</h3>
             <p className="text-slate-400 font-bold max-w-xs mx-auto mt-2 italic">Ingest files to start the multimodal journey.</p>
          </div>
        )}
      </main>

      {mediaItems.filter(i => i.processing_status === 'FAILED').length > 0 && (
      <motion.div 
        initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }}
        className="mt-24 border-t-4 border-red-50 pt-16"
      >
        <div className="flex items-center justify-between mb-10">
          <div className="flex items-center gap-4">
            <div className="bg-red-500 p-3 rounded-2xl text-white shadow-lg shadow-red-200">
              <AlertCircle className="w-6 h-6" />
            </div>
            <div>
              <h2 className="text-2xl font-black text-slate-800 tracking-tight">Vault Interruptions</h2>
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
                {mediaItems.filter(i => i.processing_status === 'FAILED').length} items require attention
              </p>
            </div>
          </div>

          <button 
            onClick={handleBulkRetry}
            className="bg-white text-red-500 border-2 border-red-500 px-8 py-3 rounded-2xl font-black text-xs hover:bg-red-500 hover:text-white transition-all shadow-xl active:scale-95 flex items-center gap-2 cursor-pointer"
          >
            <RotateCcw className="w-4 h-4" /> 
            Retry All Interruptions
          </button>
        </div>

        {/* FAILED LIST */}
        <div className="space-y-3">
          {mediaItems.filter(i => i.processing_status === 'FAILED').map((item) => (
            <motion.div 
              key={item._id}
              initial={{ x: -20, opacity: 0 }} animate={{ x: 0, opacity: 1 }}
              className="bg-white border-2 border-red-50 p-6 rounded-[32px] flex items-center justify-between group hover:border-red-200 transition-all"
            >
              <div className="flex items-center gap-6">
                <div className="w-12 h-12 bg-red-50 rounded-2xl flex items-center justify-center text-red-400">
                    {item.media_type === 'IMAGE' ? <ImageIcon className="w-5 h-5" /> : <File className="w-5 h-5" />}
                </div>
                <div>
                    <p className="text-sm font-black text-slate-800">{item.file_metadata.original_name}</p>
                    <p className="text-[10px] font-bold text-red-400 uppercase tracking-tighter mt-0.5">
                      Reason: {item.error_message || "Cloud API Timeout / Rate Limit"}
                    </p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <button 
                  onClick={() => handleRetry(item._id)}
                  className="p-3 bg-slate-50 text-slate-400 hover:bg-red-500 hover:text-white rounded-xl transition-all shadow-sm"
                  title="Retry this item"
                >
                  <RotateCcw className="w-4 h-4" />
                </button>
                <button 
                  onClick={() => handleDelete(item._id)}
                  className="p-3 bg-slate-50 text-slate-400 hover:bg-red-50 hover:text-red-500 rounded-xl transition-all shadow-sm"
                  title="Dismiss"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </motion.div>
          ))}
        </div>
      </motion.div>
    )}
    
      {/* 5. THE DEEP BREAKDOWN MODAL (Section 3) */}
      <AnimatePresence>
        {selectedItem && (
          <MediaDetailModal 
            item={selectedItem} 
            onClose={() => setSelectedItem(null)} 
          />
        )}
      </AnimatePresence>

    </div>
  );
};

export default CollectionDetail;