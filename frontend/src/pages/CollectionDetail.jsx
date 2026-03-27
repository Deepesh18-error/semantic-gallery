import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useDropzone } from 'react-dropzone';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  ArrowLeft, Upload, File, Image as ImageIcon, Video, 
  Music, Loader2, Trash2, Clock, HardDrive, Play, FileText, MousePointer2 
} from 'lucide-react';
import api from '../services/api';
import toast from 'react-hot-toast';

// --- STEP 5: SECURE IMAGE STREAMER ---
// Since files are protected by JWT, we fetch them as Blobs and create a local URL.
const SecureImage = ({ mediaId }) => {
  const [imgUrl, setImgUrl] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchProtectedImage = async () => {
      try {
        const response = await api.get(`/media/file/${mediaId}/`, { responseType: 'blob' });
        const url = URL.createObjectURL(response.data);
        setImgUrl(url);
      } catch (err) {
        console.error("Image stream failed");
      } finally {
        setLoading(false);
      }
    };
    fetchProtectedImage();
    return () => imgUrl && URL.revokeObjectURL(imgUrl); // Memory cleanup
  }, [mediaId]);

  if (loading) return <div className="w-full h-full bg-slate-100 animate-pulse" />;
  return <img src={imgUrl} alt="Vault Media" className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-700" />;
};

const CollectionDetail = () => {
  const { id: collectionId } = useParams();
  const navigate = useNavigate();

  const [collection, setCollection] = useState(null);
  const [mediaItems, setMediaItems] = useState([]);
  const [uploadQueue, setUploadQueue] = useState([]);
  const [loading, setLoading] = useState(true);

  // --- STEP 1: CONTEXTUAL HEADER FETCH ---
  useEffect(() => {
    const fetchVaultData = async () => {
      try {
        setLoading(true);
        // 1. Get collection details to theme the page
        const colRes = await api.get('/collections/');
        const found = colRes.data.find(c => c._id === collectionId);
        if (!found) return navigate('/');
        setCollection(found);

        // 2. Fetch media items for this specific collection
        // Note: Ensure your backend supports this filtering logic
        const mediaRes = await api.get('/collections/'); // Temporary: replace with actual media list endpoint
        // setMediaItems(mediaRes.data.filter(i => i.collection_id === collectionId)); 
      } catch (err) {
        toast.error("Vault access denied.");
      } finally {
        setLoading(false);
      }
    };
    fetchVaultData();
  }, [collectionId, navigate]);

  // --- STEP 2 & 3: THE MULTIPART TRUCK (Upload Logic) ---
  const onDrop = useCallback((acceptedFiles) => {
    acceptedFiles.forEach(async (file) => {
      const tempId = Math.random().toString(36).substring(7);
      
      // Add to visual queue
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
        
        // Success: Inject new item into gallery and remove from queue
        setMediaItems(prev => [res.data, ...prev]);
        setUploadQueue(q => q.filter(i => i.id !== tempId));
        toast.success(`Ingested: ${file.name}`);
      } catch (err) {
        toast.error(`Failed: ${file.name}`);
        setUploadQueue(q => q.filter(i => i.id !== tempId));
      }
    });
  }, [collectionId]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop });

  // --- STEP 6: THE ERASER (Atomic Delete) ---
  const handleDelete = async (mediaId) => {
    const originalItems = [...mediaItems];
    setMediaItems(prev => prev.filter(i => i._id !== mediaId)); // Optimistic UI

    try {
      await api.delete(`/media/delete/${mediaId}/`);
      toast.success("Item erased from physical vault.");
    } catch (err) {
      toast.error("Erase failed.");
      setMediaItems(originalItems); // Rollback
    }
  };

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <Loader2 className="animate-spin text-brand w-12 h-12" />
    </div>
  );

  return (
    <div className="min-h-screen bg-[#F8FAFC]">
      
      {/* HEADER SYSTEM */}
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
        
        {/* INGESTION ENGINE (Dropzone) */}
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

        {/* THE TRUCK (Upload Queue) */}
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

        {/* STEP 4: INTELLIGENCE-READY GRID (Gallery) */}
        {mediaItems.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
            {mediaItems.map((item) => (
              <motion.div 
                layout
                key={item._id} 
                className="group bg-white rounded-[40px] border border-slate-100 overflow-hidden hover:shadow-2xl transition-all duration-500 relative"
              >
                {/* Component Switcher */}
                <div className="aspect-[4/5] bg-slate-50 relative overflow-hidden flex items-center justify-center">
                  {item.media_type === 'IMAGE' ? (
                    <SecureImage mediaId={item._id} />
                  ) : (
                    <div className="flex flex-col items-center gap-3 text-slate-300">
                      {item.media_type === 'VIDEO' ? <Video className="w-16 h-16" /> : 
                       item.media_type === 'AUDIO' ? <Music className="w-16 h-16" /> : <FileText className="w-16 h-16" />}
                      <span className="text-[10px] font-black uppercase tracking-widest bg-slate-100 px-3 py-1 rounded-full text-slate-400">{item.media_type}</span>
                    </div>
                  )}

                  {/* Syncing Badge */}
                {item.processing_status === 'PENDING' && (
                <div className="absolute top-4 left-4 right-4 z-10 py-2 px-3 bg-slate-900/80 backdrop-blur-md rounded-2xl flex items-center gap-2 border border-white/10 shadow-xl">
                    {/* A small pulsing clock icon */}
                    <Clock className="w-3.5 h-3.5 text-brand animate-pulse" />
                    {/* Clean text badge */}
                    <span className="text-[9px] font-black uppercase tracking-widest text-white">
                    Vaulting...
                    </span>
                </div>
                )}
                </div>

                {/* Footer */}
                <div className="p-6">
                  <div className="flex justify-between items-start">
                    <div className="max-w-[70%]">
                        <p className="text-sm font-black text-slate-800 truncate">
                            {item.file_metadata?.original_name || "Unknown File"}
                            </p>

                            <p className="text-[10px] font-bold text-slate-400">
                                {((item.file_metadata?.size_bytes || 0) / 1024 / 1024).toFixed(1)} MB
                            </p>
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-tighter">
                            {(item.file_metadata.size_bytes / 1024 / 1024).toFixed(1)} MB • {item.media_type}
                        </p>
                    </div>
                    <button onClick={() => handleDelete(item._id)} className="p-3 text-slate-300 hover:text-red-500 hover:bg-red-50 rounded-2xl transition-all">
                        <Trash2 className="w-5 h-5" />
                    </button>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        ) : (
          <div className="text-center py-32 bg-white rounded-[60px] border-4 border-dashed border-slate-50">
             <HardDrive className="w-20 h-20 text-slate-100 mx-auto mb-6" />
             <h3 className="text-xl font-black text-slate-800">Vault Room is Empty</h3>
             <p className="text-slate-400 font-bold max-w-xs mx-auto mt-2 italic">Ingest files to start the multimodal journey.</p>
          </div>
        )}
      </main>
    </div>
  );
};

export default CollectionDetail;