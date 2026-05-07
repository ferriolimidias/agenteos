import axios from 'axios';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api'
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");

  if (token && token !== "null" && token !== "undefined") {
    config.headers["Authorization"] = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const requestUrl = String(error?.config?.url || "");
    const normalizedUrl = requestUrl.startsWith("/api/") ? requestUrl : `/api${requestUrl.startsWith("/") ? requestUrl : `/${requestUrl}`}`;
    const tokenInStorage = localStorage.getItem("token");
    const hasToken = Boolean(tokenInStorage && tokenInStorage !== "null" && tokenInStorage !== "undefined");
    const isAuthEndpoint = normalizedUrl.includes("/api/auth/login");
    const isPublicEndpoint = normalizedUrl.includes("/api/public/");

    // 401 em endpoint público/login não deve derrubar sessão.
    if (status !== 401 || isPublicEndpoint || isAuthEndpoint) {
      return Promise.reject(error);
    }

    // Auto-logout somente quando existe token salvo e endpoint exige sessão.
    if (hasToken) {
      localStorage.removeItem("token");

      if (window.location.pathname !== "/") {
        window.location.replace("/");
      }
    }
    return Promise.reject(error);
  }
);

export default api;
