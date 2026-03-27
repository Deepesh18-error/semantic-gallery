import axios from 'axios';
import { useAuthStore } from '../store/authStore';

// 1. Create the standard Phone instance
const api = axios.create({
  baseURL: 'http://127.0.0.1:8000/api', 
});

// 2. THE ROBOT (Request Interceptor)
api.interceptors.request.use(
  (config) => {
    // Look into the "Memory" (Zustand)
    const token = useAuthStore.getState().token;

    // If we have a token, stamp the "Envelope" (Header)
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }

    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

export default api;