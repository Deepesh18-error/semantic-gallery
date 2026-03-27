import { useState } from 'react';
import { useNavigate, Link, Navigate } from 'react-router-dom';
import { useAuthStore } from '../store/authStore';
import api from '../services/api';
import { toast } from 'react-hot-toast';
import { Mail, Lock, LogIn } from 'lucide-react';



const Login = () => {
    const navigate = useNavigate();
    const setAuth = useAuthStore((state) => state.setAuth);
    const [loading, setLoading] = useState(false);
    const[formData , setFormData] = useState({email : '' , password : ''});

    const handleSubmit = async(e) =>{
        e.preventDefault();
        setLoading(true);

        console.log("🚀 FRONTEND: Sending Login Request...");
        console.log("Payload:", formData); 

        try{
            const response = await api.post('/login/', formData);
            
            // THE MEMORY FILL: Save user and token to Zustand
            const { user, token } = response.data;
            setAuth(user, token);
            
            toast.success(`Welcome back , ${user.name}!`);
            navigate('/');
        }
        catch(error){
            console.log("Full Error Object:", error);
            console.log("Server Response Data:", error.response?.data);

            // 1. Try to get the specific "error" field we send from Django
            const messageFromBackend = error.response?.data?.error;
            
            // 2. Fallback to a default if the backend didn't send a specific message
            const finalMessage = messageFromBackend || "Invalid email or password";

            // 3. SHOW THE TOAST
            toast.error(finalMessage);


        }
        finally{
            setLoading(false);
        }
    };

  return (
        <div className="min-h-screen flex items-center justify-center p-4">
            <div className="max-w-md w-full bg-white rounded-2xl shadow-xl p-8 border border-slate-100">
                <h2 className="text-3xl font-bold text-slate-800 mb-2">Welcome Back</h2>
                <p className="text-slate-500 mb-8">Access your Multimodal Vault</p>

                <form onSubmit={handleSubmit} className="space-y-4">
                <div className="relative">
                    <Mail className="absolute left-3 top-3 text-slate-400 w-5 h-5" />
                    <input
                    type="email"
                    placeholder="Email Address"
                    required
                    className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-brand outline-none transition-all"
                    onChange={(e) => setFormData({...formData, email: e.target.value})}
                    />
                </div>

                <div className="relative">
                    <Lock className="absolute left-3 top-3 text-slate-400 w-5 h-5" />
                    <input
                    type="password"
                    placeholder="Password"
                    required
                    className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-brand outline-none transition-all"
                    onChange={(e) => setFormData({...formData, password: e.target.value})}
                    />
                </div>

                <button
                    type="submit"
                    disabled={loading}
                    className="w-full bg-brand text-slate-800 border border-black py-3 rounded-lg font-semibold cursor-pointer hover:bg-blue-600 transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
                >
                    {loading ? 'Checking...' : 'Login'}
                    <LogIn className="w-5 h-5" />
                </button>
                </form>

                <p className="mt-6 text-center text-slate-600">
                New here?{' '}
                <Link to="/register" className="text-brand font-bold hover:underline">Create an account</Link>
                </p>
            </div>
        </div>
    );
}

export default Login;