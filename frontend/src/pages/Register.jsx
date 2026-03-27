import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import api from '../services/api';
import toast from 'react-hot-toast';
import { User, Mail, Lock, ArrowRight } from 'lucide-react';

const Register = () => {
    const navigate = useNavigate();
    const[loading , setLoading] = useState(false);
    const[formData , setFormData] = useState({
        full_name: '',
        email: '',
        password: ''
    });

    const handleSubmit = async (e) =>{
        e.preventDefault(); // Stop the page from refreshing

        if (!formData.full_name.trim()) {
            return toast.error("Please enter your full name");
        }

        if (!formData.email.includes("@")) {
            return toast.error("Please enter a valid email address");
        }

        if (formData.password.length < 6) {
            return toast.error("Password must be at least 6 characters long");
        }

        setLoading(true);

        try{
            await api.post('/register/' , formData);
            toast.success('Account Created! Please Login');
            navigate('/login');
        }
        catch(error){
            console.error("Registration Error:", error.response?.data);
            
            // Check if the backend sent a specific error (like "User already exists")
            const backendError = error.response?.data?.error;
            const fallbackError = "Something went wrong. Please try again.";
            
            toast.error(backendError || fallbackError);
        }

        finally{
            setLoading(false);
        }

    };

    return(
        <div className="min-h-screen flex items-center justify-center p-4">

            <div className="max-w-md w-full bg-white rounded-2xl shadow-xl p-8 border border-slate-100">
                <h2 className="text-3xl font-bold text-slate-800 mb-2">Create Account</h2>
                <p className="text-slate-500 mb-8">Join the Multimodal Search Engine</p>


                <form onSubmit={handleSubmit} className="space-y-4">
                {/* Name Input */}
                <div className="relative">
                    <User className="absolute left-3 top-3 text-slate-400 w-5 h-5" />
                    <input
                    type="text"
                    placeholder="Full Name"
                    required
                    className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-brand outline-none transition-all"
                    onChange={(e) => setFormData({...formData, full_name: e.target.value})}
                    />
                </div>

                 {/* Email Input */}
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
                    className="w-full bg-brand text-slate-800 border border-black cursor-pointer py-3 rounded-lg font-semibold hover:bg-blue-600 transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
                >
                    {loading ? 'Creating Account...' : 'Register'}
                    <ArrowRight className="w-5 h-5" />
                </button>
        
            </form>

            <p className="mt-6 text-center text-slate-600">
                Already have an account?{' '}
                <Link to="/login" className="text-brand font-bold hover:underline">Login here</Link>
            </p>

            </div>
            
        </div>
            
    );
}

export default Register;