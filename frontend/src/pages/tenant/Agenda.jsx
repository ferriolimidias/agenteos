import "../../styles/ux05-tools.css";
import { useState, useEffect } from "react";
import api from "../../services/api";
import { Calendar, Clock, Video, User, AlertCircle, CalendarDays, CheckCircle2, X, Plus, Trash2 } from "lucide-react";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";
import { EmptyState, PageHeader } from "../../components/ui";

const DIAS_SEMANA = [
  { value: "seg", label: "Segunda" },
  { value: "ter", label: "Terça" },
  { value: "qua", label: "Quarta" },
  { value: "qui", label: "Quinta" },
  { value: "sex", label: "Sexta" },
  { value: "sab", label: "Sábado" },
  { value: "dom", label: "Domingo" },
];

const DEFAULT_EDIT_DATA = {
  dias_funcionamento: {
    seg: { aberto: true, inicio: "08:00", fim: "18:00" },
    ter: { aberto: true, inicio: "08:00", fim: "18:00" },
    qua: { aberto: true, inicio: "08:00", fim: "18:00" },
    qui: { aberto: true, inicio: "08:00", fim: "18:00" },
    sex: { aberto: true, inicio: "08:00", fim: "18:00" },
    sab: { aberto: true, inicio: "08:00", fim: "12:00" },
    dom: { aberto: false, inicio: null, fim: null },
  },
  excecoes: [],
};

export default function Agenda() {
  const [data, setData] = useState({ config: null, agendamentos: [] });
  const [loading, setLoading] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const [editData, setEditData] = useState(DEFAULT_EDIT_DATA);
  const [savingEdit, setSavingEdit] = useState(false);
  const [toast, setToast] = useState(null);

  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();

  const fetchAgenda = async () => {
    try {
      setLoading(true);
      const res = await api.get(`/empresas/${empresaId}/agenda`);
      setData(res.data);
    } catch (err) {
      console.error("Erro ao buscar dados da Agenda:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (empresaId) fetchAgenda();
    // eslint-disable-next-line
  }, [empresaId]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  if (!user) return <EmptyState title="Sessão expirada" description="Faça login novamente para acessar a agenda." />;

  const { config, agendamentos } = data;
  const hojeISO = new Date().toISOString().slice(0, 10);
  const proximosFeriados = Array.isArray(config?.excecoes)
    ? [...config.excecoes]
      .filter((item) => item?.data && item.data >= hojeISO)
      .sort((a, b) => String(a.data).localeCompare(String(b.data)))
      .slice(0, 5)
    : [];

  const normalizeTime = (value, fallback) => {
    if (!value) return fallback;
    const [hour = "", minute = ""] = String(value).split(":");
    if (!hour || !minute) return fallback;
    return `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  };

  const normalizeAgendaConfig = (rawAgenda) => {
    const normalized = { ...DEFAULT_EDIT_DATA.dias_funcionamento };
    if (!rawAgenda || typeof rawAgenda !== "object") return normalized;

    DIAS_SEMANA.forEach(({ value }) => {
      const item = rawAgenda[value];
      if (!item || typeof item !== "object") return;
      const aberto = Boolean(item.aberto);
      if (!aberto) {
        normalized[value] = { aberto: false, inicio: null, fim: null };
        return;
      }
      normalized[value] = {
        aberto: true,
        inicio: normalizeTime(item.inicio, "08:00"),
        fim: normalizeTime(item.fim, "18:00"),
      };
    });

    return normalized;
  };

  const normalizeExcecoes = (rawExcecoes) => {
    if (!Array.isArray(rawExcecoes)) return [];
    return rawExcecoes.map((item) => {
      const aberto = Boolean(item?.aberto);
      return {
        data: String(item?.data || ""),
        titulo: String(item?.titulo || ""),
        aberto,
        inicio: aberto ? normalizeTime(item?.inicio, "08:00") : null,
        fim: aberto ? normalizeTime(item?.fim, "18:00") : null,
      };
    });
  };

  const openEditModal = () => {
    const nextData = {
      dias_funcionamento: normalizeAgendaConfig(config?.dias_funcionamento),
      excecoes: normalizeExcecoes(config?.excecoes),
    };
    setEditData(nextData);
    setIsEditing(true);
  };

  const handleToggleDay = (dia) => {
    setEditData((prev) => {
      const current = prev.dias_funcionamento[dia];
      const isOpen = Boolean(current?.aberto);
      return {
        ...prev,
        dias_funcionamento: {
          ...prev.dias_funcionamento,
          [dia]: isOpen
            ? { aberto: false, inicio: null, fim: null }
            : {
              aberto: true,
              inicio: normalizeTime(current?.inicio, "08:00"),
              fim: normalizeTime(current?.fim, "18:00"),
            },
        },
      };
    });
  };

  const handleDayTimeChange = (dia, campo, valor) => {
    setEditData((prev) => ({
      ...prev,
      dias_funcionamento: {
        ...prev.dias_funcionamento,
        [dia]: {
          ...prev.dias_funcionamento[dia],
          [campo]: valor,
        },
      },
    }));
  };

  const handleAddExcecao = () => {
    setEditData((prev) => ({
      ...prev,
      excecoes: [
        ...(prev.excecoes || []),
        { data: "", titulo: "", aberto: false, inicio: null, fim: null },
      ],
    }));
  };

  const handleRemoveExcecao = (index) => {
    setEditData((prev) => ({
      ...prev,
      excecoes: (prev.excecoes || []).filter((_, idx) => idx !== index),
    }));
  };

  const handleExcecaoChange = (index, campo, valor) => {
    setEditData((prev) => ({
      ...prev,
      excecoes: (prev.excecoes || []).map((item, idx) => {
        if (idx !== index) return item;
        return { ...item, [campo]: valor };
      }),
    }));
  };

  const handleExcecaoToggleAberto = (index) => {
    setEditData((prev) => ({
      ...prev,
      excecoes: (prev.excecoes || []).map((item, idx) => {
        if (idx !== index) return item;
        const aberto = !item.aberto;
        return {
          ...item,
          aberto,
          inicio: aberto ? normalizeTime(item.inicio, "08:00") : null,
          fim: aberto ? normalizeTime(item.fim, "18:00") : null,
        };
      }),
    }));
  };

  const handleSave = async (e) => {
    e.preventDefault();
    if (!empresaId) return;

    const agendaConfig = DIAS_SEMANA.reduce((acc, { value }) => {
      const current = editData.dias_funcionamento?.[value];
      const aberto = Boolean(current?.aberto);
      acc[value] = aberto
        ? {
          aberto: true,
          inicio: normalizeTime(current?.inicio, "08:00"),
          fim: normalizeTime(current?.fim, "18:00"),
        }
        : {
          aberto: false,
          inicio: null,
          fim: null,
        };
      return acc;
    }, {});

    const diasAbertos = Object.values(agendaConfig).filter((dia) => dia.aberto);
    if (diasAbertos.length === 0) {
      setToast({ type: "error", message: "Selecione ao menos um dia de funcionamento." });
      return;
    }

    const diaInvalido = DIAS_SEMANA.find(({ value, label }) => {
      const item = agendaConfig[value];
      if (!item.aberto) return false;
      if (!item.inicio || !item.fim) return label;
      if (item.inicio >= item.fim) return label;
      return false;
    });
    if (diaInvalido) {
      setToast({
        type: "error",
        message: `O horário de "${diaInvalido.label}" está inválido (início deve ser menor que fim).`,
      });
      return;
    }

    const excecoesNormalizadas = (editData.excecoes || []).map((item) => {
      const aberto = Boolean(item?.aberto);
      return {
        data: String(item?.data || ""),
        titulo: String(item?.titulo || "").trim(),
        aberto,
        inicio: aberto ? normalizeTime(item?.inicio, "08:00") : null,
        fim: aberto ? normalizeTime(item?.fim, "18:00") : null,
      };
    });

    const excecaoInvalida = excecoesNormalizadas.find((item) => {
      if (!item.data || !item.titulo) return true;
      if (!item.aberto) return false;
      if (!item.inicio || !item.fim) return true;
      return item.inicio >= item.fim;
    });
    if (excecaoInvalida) {
      setToast({
        type: "error",
        message: "Revise as exceções: data e título são obrigatórios e, quando aberto, o início deve ser menor que o fim.",
      });
      return;
    }

    setSavingEdit(true);
    try {
      await api.put(`/empresas/${empresaId}/agenda`, {
        agenda_config: {
          dias_funcionamento: agendaConfig,
          excecoes: excecoesNormalizadas,
        },
      });
      await fetchAgenda();
      setIsEditing(false);
      setToast({ type: "success", message: "Preferências da agenda salvas com sucesso." });
    } catch (err) {
      console.error("Erro ao salvar preferências da agenda:", err);
      setToast({
        type: "error",
        message: err?.response?.data?.detail || "Não foi possível salvar as preferências da agenda.",
      });
    } finally {
      setSavingEdit(false);
    }
  };

  const formatTime = (isoString) => {
    if (!isoString) return "";
    return new Date(isoString).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="ux05-tools ux05-agenda space-y-6">
      {toast ? (
        <div className="ux05-agenda-toast fixed right-6 top-6 z-[90]" role="status" aria-live="polite">
          <div
            className={`min-w-[320px] max-w-md rounded-2xl border px-4 py-3 shadow-xl ${
              toast.type === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-red-200 bg-red-50 text-red-800"
            }`}
          >
            <div className="flex items-start gap-3">
              <div className={`rounded-full p-1 ${toast.type === "success" ? "bg-emerald-100" : "bg-red-100"}`}>
                {toast.type === "success" ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold">{toast.type === "success" ? "Tudo certo" : "Atenção"}</p>
                <p className="mt-0.5 text-sm">{toast.message}</p>
              </div>
              <button type="button" onClick={() => setToast(null)} aria-label="Fechar aviso" className="rounded-lg p-1 opacity-70 hover:opacity-100">
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <PageHeader
        title="Agenda"
        description="Acompanhe reuniões e compromissos marcados pelo atendimento, com horários, exceções e próximos eventos."
        icon={Calendar}
        eyebrow="Operação"
      />

      <div className="ux05-agenda-layout grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Lado Esquerdo: Configurações */}
        <div className="lg:col-span-1 space-y-6">
          <div className="ux05-agenda-config fm-section-card bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
            <h2 className="text-lg font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <Clock size={20} className="text-gray-400" /> 
              Configurações de Atendimento
            </h2>
            
            {!config ? (
              <div className="bg-yellow-50 text-yellow-800 p-4 rounded-xl flex items-start gap-3 border border-yellow-100">
                <AlertCircle size={20} className="text-yellow-600 flex-shrink-0 mt-0.5" />
                <div>
                  <h4 className="font-semibold text-sm">Não Configurada</h4>
                  <p className="text-xs mt-1">Sua agenda não possui horários definidos. A IA não está autorizada a realizar agendamentos no momento.</p>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Horários por Dia</span>
                  <div className="space-y-1.5">
                    {DIAS_SEMANA.map(({ value, label }) => {
                      const diaConfig = config?.dias_funcionamento?.[value];
                      const aberto = Boolean(diaConfig?.aberto);
                      return (
                        <div key={value} className="flex items-center justify-between rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-sm">
                          <span className="font-medium text-gray-700">{label}</span>
                          <span className={aberto ? "text-blue-700 font-medium" : "text-gray-400"}>
                            {aberto ? `${diaConfig.inicio} - ${diaConfig.fim}` : "Fechado"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className="pt-4 border-t border-gray-50">
                  <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Duração Padrão</span>
                  <span className="text-sm font-medium text-gray-900 bg-gray-100 px-3 py-1.5 rounded-lg inline-flex items-center gap-2">
                    <Clock size={14} className="text-gray-500"/>
                    {config.duracao_minutos} minutos
                  </span>
                </div>

                <div className="pt-4 border-t border-gray-50">
                  <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Próximos Feriados</span>
                  {proximosFeriados.length === 0 ? (
                    <p className="text-sm text-gray-400">Nenhuma data especial futura cadastrada.</p>
                  ) : (
                    <div className="space-y-2">
                      {proximosFeriados.map((item, idx) => (
                        <div key={`${item.data}-${idx}`} className="flex items-center justify-between rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-sm">
                          <div>
                            <p className="font-medium text-gray-800">{item.titulo}</p>
                            <p className="text-xs text-gray-500">
                              {new Date(`${item.data}T00:00:00`).toLocaleDateString("pt-BR")}
                            </p>
                          </div>
                          <span className={item.aberto ? "text-blue-700 font-medium" : "text-gray-400"}>
                            {item.aberto ? `${item.inicio} - ${item.fim}` : "Fechado"}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
            
            <button
              type="button"
              onClick={openEditModal}
              className="fm-button fm-button-secondary w-full mt-6 text-sm text-blue-600 font-medium bg-blue-50 py-2.5 rounded-lg hover:bg-blue-100 transition-colors"
            >
              Editar Preferências
            </button>
          </div>
        </div>

        {/* Lado Direito: Próximos Agendamentos */}
        <div className="lg:col-span-2">
          <div className="ux05-agenda-events fm-section-card bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden h-full flex flex-col">
            <div className="ux05-agenda-section-heading bg-gray-50/50 px-6 py-5 border-b border-gray-100 flex justify-between items-center">
              <h2 className="text-lg font-semibold text-gray-800 font-medium">Próximos Compromissos</h2>
              <span className="bg-green-100 text-green-700 text-xs font-bold px-3 py-1 rounded-full flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                {agendamentos.length} Agendados
              </span>
            </div>
            
            <div className="ux05-agenda-scroll flex-1 p-6 overflow-y-auto max-h-[600px] custom-scrollbar" tabIndex={0} role="region" aria-label="Próximos compromissos">
              {loading ? (
                <div className="flex justify-center items-center h-40">
                  <div className="animate-pulse flex flex-col items-center">
                    <CalendarDays className="mb-3 text-gray-300" size={40} />
                    <span className="text-gray-400 font-medium text-sm">Carregando calendário...</span>
                  </div>
                </div>
              ) : agendamentos.length === 0 ? (
                <div className="text-center flex flex-col items-center justify-center text-gray-500 h-64">
                  <div className="w-16 h-16 bg-gray-50 rounded-2xl flex items-center justify-center mb-4 border border-gray-100">
                    <CalendarDays size={32} className="text-gray-300" />
                  </div>
                  <h3 className="text-lg font-medium text-gray-900 mb-1">Pauta Limpa</h3>
                  <p className="max-w-sm text-sm">Ainda não há reuniões agendadas pela IA para os próximos dias.</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {agendamentos.map((ag) => (
                    <div key={ag.id} className="ux05-appointment group relative flex gap-4 p-4 rounded-xl border border-gray-100 hover:border-blue-200 hover:shadow-md transition-all bg-white">
                      
                      {/* Data Vertical */}
                      <div className="w-16 flex-shrink-0 flex flex-col items-center justify-center bg-gray-50 rounded-lg py-2 border border-gray-100">
                        <span className="text-green-600 font-bold text-lg leading-none mb-1">
                          {new Date(ag.data_hora_inicio).getDate().toString().padStart(2, '0')}
                        </span>
                        <span className="text-xs uppercase text-gray-500 font-semibold tracking-wider">
                          {new Date(ag.data_hora_inicio).toLocaleString('pt-BR', { month: 'short' }).replace('.', '')}
                        </span>
                      </div>
                      
                      {/* Detalhes do Evento */}
                      <div className="flex-1 min-w-0 flex flex-col justify-center">
                        <div className="ux05-appointment-title flex items-center justify-between mb-1">
                          <h4 className="font-semibold text-gray-900 text-lg flex items-center gap-2">
                            {ag.lead?.nome_contato}
                          </h4>
                          <span className="text-xs font-semibold bg-gray-100 text-gray-600 px-2 py-1 rounded-md">
                            {ag.status}
                          </span>
                        </div>
                        
                        <div className="ux05-appointment-meta flex items-center gap-4 text-sm text-gray-500">
                          <div className="flex items-center gap-1.5 font-medium text-gray-700 bg-gray-50/80 px-2 py-1 rounded border border-gray-100">
                            <Clock size={14} className="text-blue-500" />
                            {formatTime(ag.data_hora_inicio)} - {formatTime(ag.data_hora_fim)}
                          </div>
                          
                          <div className="flex items-center gap-1.5 text-gray-500">
                            <Video size={14} />
                            Google Meet
                          </div>
                        </div>
                      </div>

                      {/* Hover Actions */}
                      <div className="ux05-appointment-actions absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
                         <button aria-label="Contato do compromisso" className="bg-white border border-gray-200 text-gray-600 hover:text-blue-600 p-1.5 rounded shadow-sm">
                           <User size={16} />
                         </button>
                      </div>

                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

      </div>

      {isEditing ? (
        <div className="ux05-agenda-backdrop fixed inset-0 z-[80] flex items-center justify-center bg-gray-900/60 px-4">
          <div className="ux05-agenda-modal w-full max-w-3xl rounded-2xl border border-gray-100 bg-white p-6 shadow-2xl max-h-[90vh] overflow-y-auto" role="dialog" aria-modal="true" aria-label="Editar Preferências da Agenda">
            <div className="mb-6 flex items-start justify-between gap-4">
              <div>
                <h3 className="text-xl font-semibold text-gray-900">Editar Preferências da Agenda</h3>
                <p className="mt-1 text-sm text-gray-500">Defina os dias e horários em que sua empresa atende.</p>
              </div>
              <button
                type="button"
                onClick={() => setIsEditing(false)}
                aria-label="Fechar preferências da agenda"
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
              >
                <X size={16} />
              </button>
            </div>

            <form onSubmit={handleSave} className="space-y-5">
              <div>
                <span className="block text-sm font-medium text-gray-700 mb-2">Dias e Horários de Funcionamento</span>
                <div className="space-y-2">
                  {DIAS_SEMANA.map((dia) => {
                    const dayData = editData.dias_funcionamento?.[dia.value] || { aberto: false, inicio: null, fim: null };
                    const checked = Boolean(dayData.aberto);
                    return (
                      <div key={dia.value} className="ux05-agenda-day rounded-lg border border-gray-200 bg-white px-3 py-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-gray-700">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => handleToggleDay(dia.value)}
                              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                            />
                            {dia.label}
                          </label>
                          <span className={`text-xs font-medium ${checked ? "text-blue-700" : "text-gray-400"}`}>
                            {checked ? "Aberto" : "Fechado"}
                          </span>
                        </div>

                        {checked ? (
                          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                            <input
                              type="time"
                              aria-label={`Início de ${dia.label}`}
                              value={dayData.inicio || "08:00"}
                              onChange={(e) => handleDayTimeChange(dia.value, "inicio", e.target.value)}
                              className="fm-field w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                              required
                            />
                            <input
                              type="time"
                              aria-label={`Fim de ${dia.label}`}
                              value={dayData.fim || "18:00"}
                              onChange={(e) => handleDayTimeChange(dia.value, "fim", e.target.value)}
                              className="fm-field w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                              required
                            />
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="pt-2 border-t border-gray-100">
                <div className="mb-2 flex items-center justify-between">
                  <span className="block text-sm font-medium text-gray-700">Feriados e Datas Especiais</span>
                  <button
                    type="button"
                    onClick={handleAddExcecao}
                    className="fm-button fm-button-secondary inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  >
                    <Plus size={14} />
                    Adicionar data
                  </button>
                </div>

                {editData.excecoes?.length ? (
                  <div className="space-y-3">
                    {editData.excecoes.map((item, index) => (
                      <div key={`excecao-${index}`} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                          <input
                            type="date"
                            aria-label="Data especial"
                            value={item.data}
                            onChange={(e) => handleExcecaoChange(index, "data", e.target.value)}
                            className="fm-field w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                          />
                          <input
                            type="text"
                            value={item.titulo}
                            aria-label="Título da data especial"
                            placeholder="Título do feriado"
                            onChange={(e) => handleExcecaoChange(index, "titulo", e.target.value)}
                            className="fm-field w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100 md:col-span-2"
                          />
                        </div>

                        <div className="mt-3 flex items-center justify-between gap-2">
                          <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
                            <input
                              type="checkbox"
                              checked={Boolean(item.aberto)}
                              onChange={() => handleExcecaoToggleAberto(index)}
                              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                            />
                            Aberto?
                          </label>

                          <button
                            type="button"
                            onClick={() => handleRemoveExcecao(index)}
                            className="fm-button fm-button-danger inline-flex items-center gap-1 rounded-lg border border-red-200 bg-white px-2.5 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
                          >
                            <Trash2 size={13} />
                            Remover
                          </button>
                        </div>

                        {item.aberto ? (
                          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                            <input
                              type="time"
                              aria-label="Início da data especial"
                              value={item.inicio || "08:00"}
                              onChange={(e) => handleExcecaoChange(index, "inicio", e.target.value)}
                              className="fm-field w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                            />
                            <input
                              type="time"
                              aria-label="Fim da data especial"
                              value={item.fim || "18:00"}
                              onChange={(e) => handleExcecaoChange(index, "fim", e.target.value)}
                              className="fm-field w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                            />
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-400">Nenhuma exceção cadastrada.</p>
                )}
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setIsEditing(false)}
                  className="fm-button fm-button-secondary rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={savingEdit}
                  className="fm-button fm-button-primary rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-70"
                >
                  {savingEdit ? "Salvando..." : "Salvar"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}
