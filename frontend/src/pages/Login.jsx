import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { LogIn } from "lucide-react";
import api from "../services/api";
import { clearImpersonation } from "../utils/auth";

export default function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const response = await api.post("/auth/login", { email, senha });
      const { usuario, access_token } = response.data;
      
      // Salva os dados do usuário
      clearImpersonation();
      localStorage.setItem("user", JSON.stringify(usuario));
      localStorage.setItem("token", access_token);
      
      // Roteamento baseado na role
      if (usuario.role === "super_admin") {
        navigate("/admin");
      } else {
        navigate("/painel");
      }
    } catch (err) {
      if (err.response && err.response.data && err.response.data.detail) {
        setError(err.response.data.detail);
      } else {
        setError("Erro de rede ao conectar com o servidor.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col justify-center items-center p-4">
      <div className="bg-white p-10 rounded-2xl shadow-xl w-full max-w-md border border-gray-100">
        <div className="text-center mb-10">
          <h1 className="text-3xl font-extrabold text-gray-900 tracking-tight">Antigravity OS</h1>
          <p className="text-gray-500 mt-2">Acesso ao sistema SaaS</p>
        </div>
        
        {error && (
          <div className="mb-4 p-3 bg-red-50 text-red-600 text-sm rounded-lg border border-red-100 text-center">
            {error}
          </div>
        )}

        <form onSubmit={handleLogin} className="space-y-6 flex flex-col">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="email">Email</label>
            <input 
              id="email"
              type="email" 
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full border border-gray-300 rounded-lg py-2 px-3 focus:ring-2 flex-1 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
              placeholder="seu@email.com"
            />
          </div>
          
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="senha">Senha</label>
            <input 
              id="senha"
              type="password" 
              required
              value={senha}
              onChange={(e) => setSenha(e.target.value)}
              className="w-full border border-gray-300 rounded-lg py-2 px-3 focus:ring-2 flex-1 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
              placeholder="••••••••"
            />
          </div>
          
          <button 
            type="submit"
            disabled={loading}
            className="w-full py-3 px-4 flex items-center justify-center space-x-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium transition-colors shadow-sm disabled:opacity-70 disabled:cursor-not-allowed mt-4"
          >
            {loading ? (
              <span>Entrando...</span>
            ) : (
              <>
                <LogIn size={20} />
                <span>Entrar no Sistema</span>
              </>
            )}
          </button>
        </form>
      </div>
      
      <p className="mt-8 text-sm text-gray-400">
        Insira suas credenciais para acessar o painel administrativo.
      </p>
    </div>
  );
}
