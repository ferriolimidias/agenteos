function readJsonStorage(key) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : null;
  } catch (error) {
    console.error(`Falha ao ler ${key} do localStorage`, error);
    return null;
  }
}

export function getStoredUser() {
  return readJsonStorage("user");
}

export function getOriginalUser() {
  return readJsonStorage("original_user");
}

export function isImpersonating() {
  return localStorage.getItem("impersonating") === "true";
}

export function getAuthenticatedUser() {
  if (isImpersonating()) {
    return getOriginalUser() || getStoredUser();
  }
  return getStoredUser();
}

export function getStoredToken() {
  const token = localStorage.getItem("token");
  return token && token !== "null" && token !== "undefined" ? token : null;
}

export function getAuthenticatedToken() {
  if (isImpersonating()) {
    const originalToken = localStorage.getItem("original_token");
    if (originalToken && originalToken !== "null" && originalToken !== "undefined") {
      return originalToken;
    }
  }
  return getStoredToken();
}

export function getImpersonatedEmpresaId() {
  return localStorage.getItem("impersonated_empresa_id");
}

export function getActiveEmpresaId() {
  const impersonatedEmpresaId = getImpersonatedEmpresaId();
  if (impersonatedEmpresaId) {
    return impersonatedEmpresaId;
  }

  const user = getStoredUser();
  return user?.empresa_id || null;
}

export function clearImpersonation() {
  const originalUser = getOriginalUser();
  const originalToken = localStorage.getItem("original_token");

  if (originalUser) {
    localStorage.setItem("user", JSON.stringify(originalUser));
  }
  if (originalToken && originalToken !== "null" && originalToken !== "undefined") {
    localStorage.setItem("token", originalToken);
  }

  localStorage.removeItem("impersonating");
  localStorage.removeItem("impersonating_empresa");
  localStorage.removeItem("impersonated_empresa_id");
  localStorage.removeItem("impersonated_user_id");
  localStorage.removeItem("original_user");
  localStorage.removeItem("original_token");
}
