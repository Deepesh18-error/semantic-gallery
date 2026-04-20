import axios from 'axios';
import { useAuthStore } from '../store/authStore';

// 1. Existing instance configuration
const api = axios.create({
  baseURL: 'http://127.0.0.1:8000/api', 
});

// 2. THE ROBOT (Request Interceptor) - Keeps your JWT token attached
api.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().token;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// --- NEW PHASE 4 FUNCTIONS ---

export const searchMedia = (query, collectionId = null, fileType = null, limit = 10) => {
  return api.post('/search/', {
    query,
    collection_id: collectionId,
    file_type: fileType,
    limit
  });
};

/**
 * Fetches the last 20 searches for the logged-in user
 */
export const getSearchHistory = () => {
  return api.get('/search/history/');
};

// Export the base instance as default for general use
export default api;