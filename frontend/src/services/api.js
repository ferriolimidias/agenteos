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
    if (status === 401) {
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
