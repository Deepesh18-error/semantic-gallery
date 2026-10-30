import axios from 'axios';
import { useAuthStore } from '../store/authStore';

export const explainApiError = (error) => {
  if (!error.response) return 'Cannot reach the search service. Check your connection and try again.';
  const { status, data } = error.response;
  if (status === 401 || status === 403) return 'Your session has expired or access was denied. Sign in again.';
  if (status === 429 || data?.code === 'RATE_LIMITED') return 'The AI provider rate limit was reached. Please retry shortly.';
  if (data?.code === 'EXTRACTION_FAILED') return 'This file could not be read. Check its indexing status.';
  if (status === 503) return data?.error || 'A search service is temporarily unavailable. Please retry.';
  if (status === 400 || status === 404) return data?.error || 'The request or collection is invalid.';
  return data?.error || 'Search failed. Please retry or check the indexing status.';
};

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
