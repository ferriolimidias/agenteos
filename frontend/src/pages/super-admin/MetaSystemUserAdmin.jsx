import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound, RefreshCw, ShieldCheck, Unlink } from "lucide-react";
import api from "../../services/api";
import { ActionButton, ErrorState, Input, LoadingState, SectionCard, StatusBadge } from "../../components/ui";

const TYPE_LABELS = {
  business: "Portfólio empresarial",
  ad_account: "Conta de anúncios",
  page: "Página do Facebook",
  instagram_account: "Instagram profissional",
  dataset: "Dataset",
  pixel: "Pixel",
};

function dateLabel(value) {
  if (!value) return "Não informado";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Não informado" : date.toLocaleString("pt-BR");
}

function capabilityLabel(value) {
  if (value === "granted") return "concedida";
  if (value === "missing") return "não concedida";
  return "desconhecida";
}

export default function MetaSystemUserAdmin({ companies = [], selectedCompanyId = "" }) {
  const [credential, setCredential] = useState(null);
  const [assets, setAssets] = useState([]);
  const [linkedAssets, setLinkedAssets] = useState([]);
  const [selectedAssetIds, setSelectedAssetIds] = useState(new Set());
  const [writeEnabled, setWriteEnabled] = useState(false);
  const [token, setToken] = useState("");
  const [validation, setValidation] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [assetsError, setAssetsError] = useState("");
  const [manualAssetType, setManualAssetType] = useState("ad_account");
  const [manualAssetId, setManualAssetId] = useState("");
  const [manualBusinessId, setManualBusinessId] = useState("");
  const [datasetValidation, setDatasetValidation] = useState(null);

  const loadCredential = useCallback(async () => {
    try {
      const response = await api.get("/admin/meta/system-user/credential");
      setCredential(response.data || null);
    } catch {
      setError("Não foi possível consultar o estado da credencial central.");
    }
  }, []);

  const loadAssets = useCallback(async () => {
    try {
      const response = await api.get("/admin/meta/system-user/assets");
      setAssets(Array.isArray(response.data?.assets) ? response.data.assets : []);
      setAssetsError("");
    } catch {
      setAssetsError("Não foi possível carregar os ativos descobertos.");
    }
  }, []);

  const loadCompanyAssets = useCallback(async () => {
    if (!selectedCompanyId) {
      setLinkedAssets([]);
      setSelectedAssetIds(new Set());
      return;
    }
    try {
      const response = await api.get(`/admin/meta/system-user/companies/${selectedCompanyId}/assets`);
      const rows = Array.isArray(response.data?.assets) ? response.data.assets : [];
      setLinkedAssets(rows);
      setSelectedAssetIds(new Set(rows.map((row) => row.id)));
      setWriteEnabled(rows.some((row) => row.type === "ad_account" && row.write_enabled));
    } catch {
      setLinkedAssets([]);
      setSelectedAssetIds(new Set());
      setAssetsError("Não foi possível consultar os vínculos desta empresa.");
    }
  }, [selectedCompanyId]);

  useEffect(() => { loadCredential(); loadAssets(); }, [loadCredential, loadAssets]);
  useEffect(() => { setDatasetValidation(null); loadCompanyAssets(); }, [loadCompanyAssets]);

  const granted = useMemo(() => new Set(credential?.permissions || []), [credential]);
  const selectedCompany = companies.find((item) => item.id === selectedCompanyId);
  const toggleAsset = (asset) => {
    setSelectedAssetIds((current) => {
      const next = new Set(current);
      if (next.has(asset.id)) next.delete(asset.id);
      else next.add(asset.id);
      return next;
    });
  };

  const validateToken = async () => {
    if (!token.trim() || busy) return;
    setBusy("validate"); setError(""); setNotice(""); setValidation(null);
    try {
      const response = await api.post("/admin/meta/system-user/credential/validate", { token: token.trim() }, { timeout: 20000 });
      setValidation(response.data);
      setNotice("A Meta confirmou a credencial. Ela ainda não foi salva.");
    } catch (requestError) {
      const detail = requestError?.response?.data?.detail;
      setError(typeof detail?.message === "string" ? detail.message : "A Meta não confirmou esta credencial. A credencial salva não foi alterada.");
    } finally { setBusy(""); }
  };

  const saveToken = async () => {
    if (!token.trim() || busy) return;
    setBusy("save"); setError(""); setNotice(""); setValidation(null);
    try {
      const response = await api.put("/admin/meta/system-user/credential", { token: token.trim() }, { timeout: 20000 });
      setCredential(response.data || null);
      setToken("");
      setNotice(response.data?.existing_asset_links_invalidated
        ? "Nova credencial validada e salva criptografada. Os vínculos anteriores foram bloqueados até que os ativos sejam atualizados e vinculados novamente."
        : "Credencial validada e salva de forma criptografada. O token não será exibido novamente.");
    } catch (requestError) {
      const detail = requestError?.response?.data?.detail;
      setError(typeof detail?.message === "string" ? detail.message : typeof detail === "string" ? detail : "Não foi possível salvar a credencial. A anterior permanece intacta.");
    } finally { setBusy(""); }
  };

  const revokeToken = async () => {
    if (busy || !window.confirm("Revogar o vínculo desta credencial central no AgentOS? Isso não revoga o token na Meta. Os vínculos de ativos serão mantidos para auditoria.")) return;
    setBusy("revoke"); setError(""); setNotice("");
    try {
      await api.delete("/admin/meta/system-user/credential");
      setCredential({ configured: false, permissions: [], encryption_available: credential?.encryption_available });
      setNotice("Credencial removida do AgentOS. Para invalidá-la, revogue o token também na Meta.");
    } catch { setError("Não foi possível remover a credencial central."); }
    finally { setBusy(""); }
  };

  const discoverAssets = async () => {
    if (busy) return;
    setBusy("discover"); setError(""); setNotice("");
    try {
      const response = await api.post("/admin/meta/system-user/assets/discover", {}, { timeout: 90000 });
      setNotice(response.data?.complete
        ? `Atualização concluída: ${response.data.assets} ativos verificados.`
        : `Atualização parcial: ${response.data.assets} ativos encontrados; ${response.data.errors?.length || 0} tipos de consulta exigem permissões ou suporte da Meta.`);
      await loadAssets();
      await loadCompanyAssets();
    } catch (requestError) {
      const detail = requestError?.response?.data?.detail;
      setError(typeof detail?.message === "string" ? detail.message : "Não foi possível atualizar os ativos. Os vínculos existentes foram preservados.");
    } finally { setBusy(""); }
  };

  const validateAssetId = async () => {
    if (busy || !manualAssetId.trim()) return;
    setBusy("validate-asset"); setError(""); setNotice("");
    try {
      const response = await api.post("/admin/meta/system-user/assets/validate-id", {
        asset_type: manualAssetType,
        external_id: manualAssetId.trim(),
        business_id: manualAssetType === "business" ? null : manualBusinessId.trim(),
      }, { timeout: 30000 });
      setManualAssetId("");
      setNotice(`ID validado com a credencial central e ${response.data?.asset?.business_name || "o contexto empresarial informado"}. Nenhum acesso foi concedido a uma empresa AgentOS.`);
      await loadAssets();
    } catch (requestError) {
      const detail = requestError?.response?.data?.detail;
      setError(typeof detail?.message === "string" ? detail.message : typeof detail === "string" ? detail : "A Meta não confirmou o acesso a este ID no portfólio informado.");
    } finally { setBusy(""); }
  };

  const saveLinks = async () => {
    if (!selectedCompanyId || busy || selectedAssetIds.size === 0) return;
    setBusy("link"); setError(""); setNotice("");
    const selected = assets.filter((asset) => selectedAssetIds.has(asset.id));
    const adAccount = selected.find((asset) => asset.type === "ad_account");
    try {
      await api.put(`/admin/meta/system-user/companies/${selectedCompanyId}/assets`, {
        assets: selected.map((asset) => ({ asset_id: asset.id, write_enabled: Boolean(adAccount && asset.id === adAccount.id && writeEnabled) })),
      }, { timeout: 60000 });
      setNotice("Ativos vinculados após nova validação de acesso na Meta.");
      await loadCompanyAssets();
      await loadAssets();
    } catch (requestError) {
      const detail = requestError?.response?.data?.detail;
      setError(typeof detail?.message === "string" ? detail.message : typeof detail === "string" ? detail : "Os ativos não foram vinculados. Confira o portfólio e as permissões Meta.");
    } finally { setBusy(""); }
  };

  const validateCrmDataset = async () => {
    if (!selectedCompanyId || busy || !linkedAssets.some((asset) => asset.type === "dataset" && asset.read_enabled)) return;
    setBusy("dataset-check"); setError(""); setNotice(""); setDatasetValidation(null);
    try {
      const response = await api.post(`/admin/meta/system-user/companies/${selectedCompanyId}/datasets/crm/validate`, {}, { timeout: 30000 });
      setDatasetValidation(response.data || null);
      setNotice("Leitura do Dataset consultada. Isso não testa entrega nem otimização de conversões.");
      await loadCompanyAssets();
    } catch (requestError) {
      const detail = requestError?.response?.data?.detail;
      setError(typeof detail?.message === "string" ? detail.message : typeof detail === "string" ? detail : "Não foi possível validar a leitura do Dataset CRM.");
    } finally { setBusy(""); }
  };

  const unlinkAll = async () => {
    if (!selectedCompanyId || busy || !window.confirm("Desvincular os ativos Meta desta empresa no AgentOS? Isso não altera ativos nem campanhas na Meta.")) return;
    setBusy("unlink"); setError(""); setNotice("");
    try {
      await api.delete(`/admin/meta/system-user/companies/${selectedCompanyId}/assets`);
      setLinkedAssets([]); setSelectedAssetIds(new Set()); setWriteEnabled(false);
      setNotice("Ativos desvinculados desta empresa no AgentOS. Os dados e as campanhas na Meta não foram alterados.");
      await loadAssets();
    } catch { setError("Não foi possível desvincular os ativos."); }
    finally { setBusy(""); }
  };

  return <>
    <SectionCard title="Meta · Usuário do Sistema" description="Cadastre uma única credencial central da Ferrioli. O AgentOS valida o token no Graph API e o armazena criptografado; empresas recebem apenas vínculos explícitos dos ativos.">
      {error ? <p role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}
      {notice ? <p role="status" className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">{notice}</p> : null}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.8fr)]">
        <div className="space-y-3">
          <label className="fm-admin-field"><span>Token de usuário do sistema</span><Input type="password" autoComplete="new-password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Cole o token para validar ou substituir" disabled={!credential?.encryption_available && credential?.encryption_available !== undefined} /></label>
          <p className="text-xs text-slate-600">O campo não é preenchido com o segredo salvo e não usa armazenamento do navegador. Uma substituição só é salva depois que a Meta confirma o novo token.</p>
          <div className="flex flex-wrap gap-2">
            <ActionButton type="button" variant="secondary" onClick={validateToken} disabled={!token.trim() || Boolean(busy)} icon={ShieldCheck}>{busy === "validate" ? "Validando..." : "Validar token"}</ActionButton>
            <ActionButton type="button" onClick={saveToken} disabled={!token.trim() || Boolean(busy) || credential?.encryption_available === false} icon={KeyRound}>{busy === "save" ? "Validando e salvando..." : "Salvar credencial"}</ActionButton>
            <ActionButton type="button" variant="secondary" onClick={() => { setToken(""); setValidation(null); }} disabled={!token && !validation}>Limpar campo</ActionButton>
          </div>
      {validation ? <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950"><strong>Validação sem gravação</strong><p>{validation.system_user_name || "Identidade retornada pela Meta"} · ID {validation.system_user_id || "não informado"}</p><p className="mt-1">Tipo System User / vínculo com a BM Ferrioli: não verificado por esta resposta. Aplicativo: {validation.app_name || validation.app_id || "não identificado"} ({validation.app_binding_state === "verified" ? "vínculo do app verificado" : "vínculo não verificado"}) · Validade: {validation.expiry_status === "no_expiration_reported" ? "sem expiração reportada" : dateLabel(validation.expires_at)}</p><p className="mt-1">Permissões: {validation.permissions_status === "verified" ? (validation.permissions || []).join(", ") || "nenhuma concedida retornada" : "não verificadas"}</p></div> : null}
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
          <h3 className="font-semibold text-slate-900">Estado da credencial</h3>
          {!credential ? <LoadingState label="Consultando o estado sem revelar o segredo..." /> : <div className="mt-3 space-y-2 text-sm">
            <StatusBadge tone={credential.configured && credential.validated ? "success" : "warning"}>{credential.configured ? credential.validated ? "Token conectado e validado" : "Token armazenado; validação pendente" : "Sem token central cadastrado"}</StatusBadge>
            {credential.token_masked ? <p>Identificação: <code>{credential.token_masked}</code></p> : null}
            {credential.system_user_name ? <p>Identidade retornada pela Meta: {credential.system_user_name} · {credential.system_user_id} · Tipo System User/BM: não verificado</p> : null}
            <p>Aplicativo: {credential.app_name || credential.app_id || "não identificado"}</p>
            <p>Validade: {credential.expiry_status === "no_expiration_reported" ? "sem expiração reportada pela Meta" : credential.expires_at ? dateLabel(credential.expires_at) : "não informada pela Meta"}</p>
            <p>Última validação: {dateLabel(credential.validated_at)}</p>
            <p>Criptografia disponível: {credential.encryption_available ? "sim" : "não"}</p>
              <p>Permissões: {credential.permissions_status === "verified" ? (credential.permissions || []).join(", ") || "nenhuma concedida retornada" : `não verificadas (${credential.permissions_error_code || "estado desconhecido"})`}</p>
              <div className="rounded-md border border-slate-200 bg-white p-3">
                <p className="font-medium">Capacidades observadas</p>
                <ul className="mt-1 grid gap-1 text-xs sm:grid-cols-2">
                  <li>Leitura de anúncios: {capabilityLabel(credential.permission_capabilities?.ads_read)}</li>
                  <li>Gerenciamento de anúncios: {capabilityLabel(credential.permission_capabilities?.ads_management)}</li>
                  <li>Gerenciamento de negócios: {capabilityLabel(credential.permission_capabilities?.business_management)}</li>
                  <li>Listagem de Páginas: {capabilityLabel(credential.permission_capabilities?.pages_show_list)}</li>
                  <li>Leitura de publicações: {capabilityLabel(credential.permission_capabilities?.pages_read_engagement)}</li>
                  <li>Instagram profissional: {capabilityLabel(credential.permission_capabilities?.instagram_basic)}</li>
                </ul>
                <p className="mt-2 text-xs text-slate-600">Permissões concedidas não comprovam acesso a cada ativo nem autorização de escrita. O AgentOS verifica o ativo e a associação da empresa separadamente.</p>
              </div>
            {credential.configured ? <ActionButton type="button" variant="danger" onClick={revokeToken} disabled={Boolean(busy)} icon={Unlink}>{busy === "revoke" ? "Removendo..." : "Remover do AgentOS"}</ActionButton> : null}
            {credential.configured ? <p className="text-xs text-slate-600">Remover aqui não revoga o token na Meta. A revogação externa deve ser feita no Business Manager.</p> : null}
          </div>}
        </div>
      </div>
    </SectionCard>

    <SectionCard title="Ativos acessíveis e vínculos por empresa" description="A descoberta consulta apenas ativos retornados pela Graph API. Uma falha parcial indica consulta não autorizada ou indisponível; não equivale a confirmar que o ativo não existe.">
      {assetsError ? <ErrorState title="Ativos indisponíveis" description={assetsError} /> : null}
      <div className="flex flex-wrap gap-2">
        <ActionButton type="button" variant="secondary" onClick={discoverAssets} disabled={Boolean(busy) || !credential?.configured} icon={RefreshCw}>{busy === "discover" ? "Consultando a Meta..." : "Atualizar ativos Meta"}</ActionButton>
        <ActionButton type="button" variant="secondary" onClick={loadAssets} disabled={Boolean(busy)}>Atualizar lista salva</ActionButton>
      </div>
      {!credential?.configured ? <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">Cadastre e valide o token para descobrir ativos. Não é necessário um token Marketing por empresa.</p> : null}
      <div className="mt-4 grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 sm:grid-cols-2 xl:grid-cols-4">
        <label className="fm-admin-field"><span>Tipo de ativo para validar</span><select className="fm-input" value={manualAssetType} onChange={(event) => setManualAssetType(event.target.value)} disabled={Boolean(busy)}>{Object.entries(TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="fm-admin-field"><span>ID do ativo</span><Input inputMode="numeric" value={manualAssetId} onChange={(event) => setManualAssetId(event.target.value)} placeholder="ID Meta" disabled={Boolean(busy)} /></label>
        {manualAssetType !== "business" ? <label className="fm-admin-field"><span>Business ID de acesso</span><Input inputMode="numeric" value={manualBusinessId} onChange={(event) => setManualBusinessId(event.target.value)} placeholder="ID do portfólio empresarial" disabled={Boolean(busy)} /></label> : null}
        <div className="flex items-end"><ActionButton type="button" variant="secondary" onClick={validateAssetId} disabled={!credential?.configured || !manualAssetId.trim() || Boolean(busy) || (manualAssetType !== "business" && !manualBusinessId.trim())}>{busy === "validate-asset" ? "Validando ID..." : "Validar ID na Meta"}</ActionButton></div>
        <p className="text-xs text-slate-600 sm:col-span-2 xl:col-span-4">A validação consulta o ativo através do portfólio informado e não o vincula automaticamente a nenhuma empresa. IDs sem acesso confirmado não entram no catálogo.</p>
      </div>
      {selectedCompany ? <div className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold text-slate-900">Vínculos de {selectedCompany.nome_empresa}</h3><p className="text-xs text-slate-600">Contas, Páginas, Instagram, Datasets e Pixels ficam exclusivos da empresa vinculada. O portfólio empresarial compartilhado pode servir de contexto para mais de uma empresa. O backend revalida o acesso antes de salvar.</p></div><span className="text-xs text-slate-600">{linkedAssets.length} vínculo(s) atual(is)</span></div>
        {assets.length === 0 ? <p className="mt-3 text-sm text-slate-600">Nenhum ativo descoberto. Atualize a lista depois de conectar o Usuário do Sistema.</p> : <div className="mt-3 max-h-[32rem] overflow-y-auto rounded border border-slate-100">
          {assets.map((asset) => {
            const linkedElsewhere = asset.type !== "business" && asset.linked_empresa_id && asset.linked_empresa_id !== selectedCompanyId;
            const checked = selectedAssetIds.has(asset.id);
            const sameTypeSelected = Array.from(selectedAssetIds).some((id) => id !== asset.id && assets.find((candidate) => candidate.id === id)?.type === asset.type);
            return <label key={asset.id} className={`flex flex-col gap-2 border-b border-slate-100 p-3 sm:flex-row sm:items-start ${linkedElsewhere ? "opacity-60" : ""}`}>
              <input type="checkbox" className="mt-1" checked={checked} disabled={Boolean(linkedElsewhere) || asset.access_state !== "verified" || Boolean(busy) || (!checked && sameTypeSelected)} onChange={() => toggleAsset(asset)} />
              <span className="min-w-0 flex-1"><strong className="block">{asset.name || TYPE_LABELS[asset.type] || "Ativo Meta"}</strong><span className="block text-xs text-slate-600">{asset.type === "dataset" ? (asset.metadata?.is_crm === true ? "Dataset CRM" : "Dataset · finalidade não confirmada") : TYPE_LABELS[asset.type] || asset.type} · {asset.external_id}</span><span className="block text-xs text-slate-500">Portfólios que deram acesso: {(asset.business_access || []).map((business) => business.business_name || business.business_id).filter(Boolean).join(", ") || asset.business_name || asset.business_id || "não confirmado"} · Acesso: {asset.access_path || "não informado"} · Verificado: {dateLabel(asset.last_verified_at)}</span>{asset.type === "dataset" && asset.metadata?.is_unavailable === true ? <span className="block text-xs text-amber-700">A Meta informou que este Dataset está indisponível.</span> : null}{asset.metadata?.owner_business_name ? <span className="block text-xs text-slate-500">Portfólio proprietário informado pela Meta: {asset.metadata.owner_business_name}</span> : null}{linkedElsewhere ? <span className="block text-xs text-amber-700">Vinculado a outra empresa</span> : null}{asset.type === "business" && (asset.linked_empresa_ids || []).some((id) => id !== selectedCompanyId) ? <span className="block text-xs text-blue-700">Portfólio compartilhado no AgentOS</span> : null}</span>
              <StatusBadge tone={asset.access_state === "verified" ? "success" : "warning"}>{asset.access_state === "verified" ? "Acesso consultado" : "Acesso não confirmado"}</StatusBadge>
            </label>;
          })}
        </div>}
        {Array.from(selectedAssetIds).some((id) => assets.find((asset) => asset.id === id)?.type === "ad_account") ? <label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={writeEnabled} disabled={!granted.has("ads_management") || Boolean(busy)} onChange={(event) => setWriteEnabled(event.target.checked)} /><span>Permitir criação explícita de objetos de anúncios pausados para a conta selecionada. Requer <code>ads_management</code>, vínculo verificado e controles operacionais ativos. {granted.has("ads_management") ? "A permissão Meta foi confirmada." : "A permissão Meta não foi confirmada; escrita segue bloqueada."}</span></label> : null}
        {linkedAssets.some((asset) => asset.type === "dataset" && asset.read_enabled) ? <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><h4 className="font-medium text-slate-900">Dataset de eventos CRM</h4><p className="text-xs text-slate-700">A validação faz apenas um GET para confirmar acesso e sinalização CRM. Não envia eventos CAPI e não comprova entrega ou otimização.</p></div><ActionButton type="button" variant="secondary" onClick={validateCrmDataset} disabled={Boolean(busy)}>{busy === "dataset-check" ? "Consultando Dataset..." : "Validar leitura do Dataset CRM"}</ActionButton></div>{datasetValidation ? <div className="mt-3 space-y-1 text-sm" role="status"><p>Leitura Graph: <strong>confirmada agora</strong> · Dataset: {datasetValidation.dataset?.name || datasetValidation.dataset?.id || "identidade não informada"}</p><p>Finalidade CRM: {datasetValidation.crm_dataset_state === "observed" ? "confirmada" : datasetValidation.crm_dataset_state === "not_applicable" ? "não é um Dataset CRM" : datasetValidation.crm_dataset_state === "partial" ? "Dataset indisponível" : "desconhecida"}</p><p>Permissões do aplicativo: {datasetValidation.system_user_permissions_state === "verified" ? (datasetValidation.system_user_permissions || []).join(", ") || "nenhuma permissão retornada" : "não verificadas"}</p><p>Entrega CAPI / otimização: <strong>não comprovada</strong>. Sender LIVE desativado.</p></div> : null}</div> : null}
        <div className="mt-3 flex flex-wrap gap-2"><ActionButton type="button" onClick={saveLinks} disabled={!selectedCompanyId || selectedAssetIds.size === 0 || Boolean(busy)}>{busy === "link" ? "Validando vínculos..." : "Salvar vínculos da empresa"}</ActionButton>{linkedAssets.length ? <ActionButton type="button" variant="danger" onClick={unlinkAll} disabled={Boolean(busy)}>{busy === "unlink" ? "Desvinculando..." : "Desvincular todos os ativos"}</ActionButton> : null}</div>
      </div> : <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-700">Selecione uma empresa na seção de estado por empresa abaixo para configurar seus ativos.</p>}
    </SectionCard>
  </>;
}
