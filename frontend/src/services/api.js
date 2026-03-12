import axios from 'axios';

const api = axios.create({
  baseURL: 'http://localhost:8000/api'
});

api.interceptors.request.use((config) => {
  const userStr = localStorage.getItem('user');
  if (userStr) {
    try {
      const user = JSON.parse(userStr);
      if (user.role) {
        config.headers['X-User-Role'] = user.role;
      }
    } catch (e) {}
  }
  return config;
});

export default api;
