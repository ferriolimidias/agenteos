export function getStoredUser() {
  try {
    const userStr = localStorage.getItem("user");
    return userStr ? JSON.parse(userStr) : null;
  } catch (error) {
    console.error("Falha ao ler usuário do localStorage", error);
    return null;
  }
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
  localStorage.removeItem("impersonating");
  localStorage.removeItem("impersonating_empresa");
  localStorage.removeItem("impersonated_empresa_id");
  localStorage.removeItem("impersonated_user_id");
  localStorage.removeItem("original_user");
  localStorage.removeItem("original_token");
}
