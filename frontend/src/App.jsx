import React from 'react';
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import Register from './pages/Register';
import Login from './pages/Login';
import { Toaster } from 'react-hot-toast';
import { useAuthStore } from './store/authStore';
import Dashboard from './pages/Dashboard';
import CollectionDetail from './pages/CollectionDetail';
import Settings from './pages/Settings';
import Search from './pages/Search';

function App() {
  const token = useAuthStore((state) => state.token); 
  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <Toaster position="top-center" containerStyle={{ zIndex: 99999 }} />
      
      <Routes>
        {/* EXISTING ROUTES */}
        <Route path="/" element={token ? <Dashboard /> : <Navigate to="/login" replace />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        
        {/* NEW SEARCH ROUTE */}
        <Route 
          path="/search" 
          element={token ? <Search /> : <Navigate to="/login" replace />} 
        />

        <Route path="/settings" element={token ? <Settings /> : <Navigate to="/login" replace />} />
        <Route path="/collection/:id" element={token ? <CollectionDetail /> : <Navigate to="/login" replace />} />
      </Routes>
    </div>
  );
}

export default App;