import axios from 'axios';
import { getStoredUser } from "../utils/auth";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api'
});

api.interceptors.request.use((config) => {
  const user = getStoredUser();
  const token = localStorage.getItem("token");

  if (token) {
    config.headers["Authorization"] = `Bearer ${token}`;
  }

  if (user) {
    if (user.role) {
      config.headers['X-User-Role'] = user.role;
    }
    if (user.id) {
      config.headers['X-User-Id'] = user.id;
    }
  }
  return config;
});

export default api;
