import axios from 'axios';
import { getAuthenticatedToken } from "../utils/auth";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api'
});

api.interceptors.request.use((config) => {
  const token = getAuthenticatedToken();

  if (token) {
    config.headers["Authorization"] = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const requestUrl = String(error?.config?.url || "");
    const hasAuthHeader = Boolean(error?.config?.headers?.Authorization);
    const isAuthEndpoint = requestUrl.includes("/auth/login");
    const isPublicEndpoint = requestUrl.includes("/public/");

    // Evita logout agressivo por 401 em rotas públicas ou login.
    // Só forçamos logout em falhas autenticadas de sessão.
    if (status === 401 && hasAuthHeader && !isAuthEndpoint && !isPublicEndpoint) {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      localStorage.removeItem("impersonating");
      localStorage.removeItem("impersonating_empresa");
      localStorage.removeItem("impersonated_empresa_id");
      localStorage.removeItem("impersonated_user_id");
      localStorage.removeItem("original_user");
      localStorage.removeItem("original_token");

      if (window.location.pathname !== "/") {
        window.location.replace("/");
      }
    }
    return Promise.reject(error);
  }
);

export default api;
