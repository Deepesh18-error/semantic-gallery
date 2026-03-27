import { create } from 'zustand';

export const useCollectionStore = create((set) => ({
  collections: [],
  activeCollection: null,

  setCollections: (collections) => set({ collections }),

  setActiveCollection: (collection) => set({ activeCollection: collection }),
  
  addCollection: (collection) => 
    set((state) => ({ collections: [...state.collections, collection] })),

  // --- ADD THIS ACTION ---
  clearStore: () => set({ collections: [], activeCollection: null }),
}));