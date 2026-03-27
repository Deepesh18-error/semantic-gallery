import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export const useAuthStore = create(
  persist(
    (set) => ({
      user: null,   // Holds { id, name, email }
      token: null,  // Holds the JWT "Wristband" string

      // Action: When a user logs in successfully
      setAuth: (user, token) => set({ user, token }),

      // Action: When a user clicks "Logout"
      logout: () => set({ user: null, token: null }),
    }),
    {
      name: 'auth-storage', // The name of the key in LocalStorage
    }
  )
);