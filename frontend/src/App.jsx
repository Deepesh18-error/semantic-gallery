import React from 'react';
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import Register from './pages/Register';
import Login from './pages/Login';
import { Toaster } from 'react-hot-toast';
import { useAuthStore } from './store/authStore';

const Dashboard = () => {
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);

  return (
    <div className="p-10">
      <h1 className="text-3xl font-bold text-slate-800">
        Welcome back, <span className="text-brand">{user?.name}</span>! 🎉
      </h1>
      <p className="text-slate-500 mt-2">Your Multimodal Vault is ready.</p>
      
      <button 
        onClick={logout}
        className="mt-6 px-4 py-2 bg-red-100 text-red-600 rounded-lg font-medium hover:bg-red-200 transition-colors"
      >
        Logout
      </button>
    </div>
  );
};


function App() {
  const navigate = useNavigate();
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
          element={token ? <Dashboard /> : <Navigate to="/login" />}
        />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
      </Routes>
    </div>
  );
}

export default App;
