const KNOWN_MESSAGES = {
  META_EXISTING_POST_ADS_DISABLED: "A seleção de publicações existentes está indisponível até a integração ser liberada pelo operador.",
  META_CREDENTIAL_INVALID: "A credencial de anúncios da empresa foi recusada ou expirou. Reautorize a conexão Meta antes de tentar novamente.",
  META_READ_PERMISSION_REQUIRED: "A credencial da empresa não tem permissão para ler estas publicações.",
  META_RATE_LIMIT: "A Meta limitou temporariamente as consultas. Aguarde um pouco e tente novamente.",
  META_READ_TIMEOUT: "A consulta demorou mais que o permitido. Tente novamente.",
  META_READ_TEMPORARILY_UNAVAILABLE: "A Meta está temporariamente indisponível. Tente novamente mais tarde.",
};

const GRAPH_CODE_MESSAGES = {
  invalid_auth: ["A credencial de anúncios da empresa foi recusada ou expirou. Reautorize a conexão Meta antes de tentar novamente.", false],
  token_expired: ["A credencial de anúncios da empresa foi recusada ou expirou. Reautorize a conexão Meta antes de tentar novamente.", false],
  missing_permission: ["A credencial da empresa não tem permissão para ler estas publicações.", false],
  rate_limit: ["A Meta limitou temporariamente as consultas. Aguarde um pouco e tente novamente.", true],
  timeout: ["A consulta demorou mais que o permitido. Tente novamente.", true],
  temporary_error: ["A Meta está temporariamente indisponível. Tente novamente mais tarde.", true],
};

export function describeMetaReadError(error) {
  const status = Number(error?.response?.status || 0);
  const code = error?.response?.data?.detail?.code;
  if (GRAPH_CODE_MESSAGES[code]) {
    const [message, retryable] = GRAPH_CODE_MESSAGES[code];
    return { message, retryable };
  }
  if (KNOWN_MESSAGES[code]) {
    return {
      message: KNOWN_MESSAGES[code],
      retryable: ["META_RATE_LIMIT", "META_READ_TIMEOUT", "META_READ_TEMPORARILY_UNAVAILABLE"].includes(code),
    };
  }
  if (!error?.response) {
    return { message: "Falha de rede ao consultar a Meta. Verifique a conexão e tente novamente.", retryable: true };
  }
  if (status === 401) {
    return { message: "Sua sessão ou autorização expirou. Atualize o acesso e tente novamente.", retryable: false };
  }
  if (status === 403) {
    return { message: "A Página ou conta selecionada não está autorizada para esta empresa.", retryable: false };
  }
  if (status === 409) {
    return { message: "Revise a conta de anúncios e os vínculos da Página antes de continuar.", retryable: false };
  }
  if (status === 429) {
    return { message: "A Meta limitou temporariamente as consultas. Aguarde um pouco e tente novamente.", retryable: true };
  }
  if (status === 408 || status >= 500) {
    return { message: "A consulta está temporariamente indisponível. Tente novamente mais tarde.", retryable: true };
  }
  return { message: "Não foi possível concluir a consulta de leitura à Meta.", retryable: false };
}
