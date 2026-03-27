import { create } from 'zustand';

export const useCollectionStore = create((set) => ({
  collections: [],
  activeCollection: null,

  // Action: Set the full list of collections from the server
  setCollections: (collections) => set({ collections }),

  // Action: Set which folder we are currently looking at
  setActiveCollection: (collection) => set({ activeCollection: collection }),

  // Action: Add a single new collection to the existing list
  addCollection: (collection) => 
    set((state) => ({ collections: [...state.collections, collection] })),
}));