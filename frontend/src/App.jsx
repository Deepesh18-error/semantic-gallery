import React from 'react';
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import Register from './pages/Register';
import Login from './pages/Login';
import { Toaster } from 'react-hot-toast';
import { useAuthStore } from './store/authStore';
import Dashboard from './pages/Dashboard';



function App() {
  const token = useAuthStore((state) => state.token); 
  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <Toaster 
        position="top-center" 
        containerStyle={{ zIndex: 99999 }} // Forces it to the front
      />
      
      <Routes>
        <Route 
          path="/" 
          element={token ? <Dashboard /> : <Navigate to="/login" replace />} 
        />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
      </Routes>
    </div>
  );
}

export default App;
