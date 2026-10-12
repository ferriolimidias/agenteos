import axios from 'axios';
import { getActiveEmpresaId } from '../utils/auth';
import { crmSaveError, isCrmFieldsSaveRequest } from '../utils/crmLeadSave';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api'
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");

  if (config.crmFieldsSaveContext) {
    if (!isCrmFieldsSaveRequest(config) || config.baseURL !== api.defaults.baseURL) {
      return Promise.reject(crmSaveError('CRM_CONTEXT_CHANGED'));
    }
    const authorization = token && token !== 'null' && token !== 'undefined' ? `Bearer ${token}` : '';
    if (getActiveEmpresaId() !== config.crmFieldsSaveContext.empresaId || (config.headers?.Authorization || '') !== authorization) {
      return Promise.reject(crmSaveError('CRM_CONTEXT_CHANGED'));
    }
  }

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
    if (status === 401) {
      console.warn("401 detectado em:", error?.config?.url);
    }
    // A rejected fields-save must reach its editor. Never convert 401 to success.
    // Tags, other endpoints and callers without this exact opt-in retain logout.
    if (status === 401 && isCrmFieldsSaveRequest(error?.config) && error.config.baseURL === api.defaults.baseURL) {
      return Promise.reject(error);
    }
    const tokenInStorage = localStorage.getItem("token");
    const hasToken = Boolean(tokenInStorage && tokenInStorage !== "null" && tokenInStorage !== "undefined");
    const isAuthEndpoint = normalizedUrl.includes("/api/auth/login");
    const isPublicEndpoint = normalizedUrl.includes("/api/public/");
    const isEvolutionEndpoint = normalizedUrl.toLowerCase().includes("evolution");

    // 401 em endpoint público/login não deve derrubar sessão.
    if (status !== 401 || isPublicEndpoint || isAuthEndpoint || isEvolutionEndpoint) {
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
