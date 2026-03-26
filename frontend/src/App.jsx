import React from 'react';
import { Routes, Route } from 'react-router-dom';

// We will create real pages for these in the next step
const Dashboard = () => <div className="p-10 text-2xl font-bold text-blue-600">Dashboard (GPS Ready! 🛰️)</div>;
const Login = () => <div className="p-10 text-2xl font-bold text-green-600">Login (GPS Ready! 🛰️)</div>;

function App() {
  return (
    <div className="min-h-screen bg-slate-100">
      {/* 
        Intuition: The Routes component is the "Switchboard". 
        It looks at the URL and renders ONLY the matching Route.
      */}
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/login" element={<Login />} />
      </Routes>
    </div>
  );
}

export default App;