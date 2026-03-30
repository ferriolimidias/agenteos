import { useState, useEffect } from "react";
import api from "../../services/api";
import { Calendar, Clock, Video, User, AlertCircle, CalendarDays, CheckCircle2, X } from "lucide-react";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

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
  inicio: "08:00",
  fim: "18:00",
  dias: ["seg", "ter", "qua", "qui", "sex"],
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

  if (!user) return <div className="p-8 text-center text-gray-500">Faça login novamente.</div>;

  const { config, agendamentos } = data;

  const normalizeTime = (value, fallback) => {
    if (!value) return fallback;
    const [hour = "", minute = ""] = String(value).split(":");
    if (!hour || !minute) return fallback;
    return `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  };

  const openEditModal = () => {
    const nextData = {
      inicio: normalizeTime(config?.inicio, DEFAULT_EDIT_DATA.inicio),
      fim: normalizeTime(config?.fim, DEFAULT_EDIT_DATA.fim),
      dias: Array.isArray(config?.dias) && config.dias.length > 0 ? config.dias : DEFAULT_EDIT_DATA.dias,
    };
    setEditData(nextData);
    setIsEditing(true);
  };

  const handleToggleDay = (dia) => {
    setEditData((prev) => {
      const hasDay = prev.dias.includes(dia);
      return {
        ...prev,
        dias: hasDay ? prev.dias.filter((item) => item !== dia) : [...prev.dias, dia],
      };
    });
  };

  const handleSave = async (e) => {
    e.preventDefault();
    if (!empresaId) return;

    if (editData.dias.length === 0) {
      setToast({ type: "error", message: "Selecione ao menos um dia de funcionamento." });
      return;
    }

    if (editData.inicio >= editData.fim) {
      setToast({ type: "error", message: "O horário de início deve ser menor que o horário de fim." });
      return;
    }

    setSavingEdit(true);
    try {
      await api.put(`/empresas/${empresaId}/agenda`, {
        agenda_config: {
          dias_funcionamento: editData.dias,
          horario_inicio: editData.inicio,
          horario_fim: editData.fim,
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

  const formatDate = (isoString) => {
    if (!isoString) return "";
    return new Date(isoString).toLocaleDateString("pt-BR", { day: '2-digit', month: 'short', year: 'numeric' });
  };

  return (
    <div className="space-y-6">
      {toast ? (
        <div className="fixed right-6 top-6 z-[90]">
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
              <button type="button" onClick={() => setToast(null)} className="rounded-lg p-1 opacity-70 hover:opacity-100">
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div>
        <h1 className="text-3xl font-bold text-gray-900 tracking-tight flex items-center gap-3">
          <Calendar className="text-blue-600" size={32} />
          Agenda Híbrida
        </h1>
        <p className="text-gray-500 mt-1">
          Acompanhe as reuniões e compromissos marcados automaticamente pelo seu Agente.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Lado Esquerdo: Configurações */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
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
                  <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Dias Ativos</span>
                  <div className="flex flex-wrap gap-2">
                    {['seg', 'ter', 'qua', 'qui', 'sex', 'sab', 'dom'].map(dia => (
                      <span 
                        key={dia} 
                        className={`px-3 py-1 rounded-full text-xs font-medium uppercase ${config.dias?.includes(dia) ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-400'}`}
                      >
                        {dia}
                      </span>
                    ))}
                  </div>
                </div>
                
                <div className="grid grid-cols-2 gap-4 pt-4 border-t border-gray-50">
                  <div>
                    <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Início</span>
                    <span className="text-lg font-medium text-gray-900">{config.inicio}</span>
                  </div>
                  <div>
                    <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Fim</span>
                    <span className="text-lg font-medium text-gray-900">{config.fim}</span>
                  </div>
                </div>
                
                <div className="pt-4 border-t border-gray-50">
                  <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Duração Padrão</span>
                  <span className="text-sm font-medium text-gray-900 bg-gray-100 px-3 py-1.5 rounded-lg inline-flex items-center gap-2">
                    <Clock size={14} className="text-gray-500"/>
                    {config.duracao_minutos} minutos
                  </span>
                </div>
              </div>
            )}
            
            <button
              type="button"
              onClick={openEditModal}
              className="w-full mt-6 text-sm text-blue-600 font-medium bg-blue-50 py-2.5 rounded-lg hover:bg-blue-100 transition-colors"
            >
              Editar Preferências
            </button>
          </div>
        </div>

        {/* Lado Direito: Próximos Agendamentos */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden h-full flex flex-col">
            <div className="bg-gray-50/50 px-6 py-5 border-b border-gray-100 flex justify-between items-center">
              <h2 className="text-lg font-semibold text-gray-800 font-medium">Próximos Compromissos</h2>
              <span className="bg-green-100 text-green-700 text-xs font-bold px-3 py-1 rounded-full flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                {agendamentos.length} Agendados
              </span>
            </div>
            
            <div className="flex-1 p-6 overflow-y-auto max-h-[600px] custom-scrollbar">
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
                    <div key={ag.id} className="group relative flex gap-4 p-4 rounded-xl border border-gray-100 hover:border-blue-200 hover:shadow-md transition-all bg-white">
                      
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
                        <div className="flex items-center justify-between mb-1">
                          <h4 className="font-semibold text-gray-900 text-lg flex items-center gap-2">
                            {ag.lead?.nome_contato}
                          </h4>
                          <span className="text-xs font-semibold bg-gray-100 text-gray-600 px-2 py-1 rounded-md">
                            {ag.status}
                          </span>
                        </div>
                        
                        <div className="flex items-center gap-4 text-sm text-gray-500">
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
                      <div className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
                         <button className="bg-white border border-gray-200 text-gray-600 hover:text-blue-600 p-1.5 rounded shadow-sm">
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
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-gray-900/60 px-4">
          <div className="w-full max-w-lg rounded-2xl border border-gray-100 bg-white p-6 shadow-2xl">
            <div className="mb-6 flex items-start justify-between gap-4">
              <div>
                <h3 className="text-xl font-semibold text-gray-900">Editar Preferências da Agenda</h3>
                <p className="mt-1 text-sm text-gray-500">Defina os dias e horários em que sua empresa atende.</p>
              </div>
              <button
                type="button"
                onClick={() => setIsEditing(false)}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
              >
                <X size={16} />
              </button>
            </div>

            <form onSubmit={handleSave} className="space-y-5">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <label className="text-sm font-medium text-gray-700">
                  Horário de Início
                  <input
                    type="time"
                    value={editData.inicio}
                    onChange={(e) => setEditData((prev) => ({ ...prev, inicio: e.target.value }))}
                    className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    required
                  />
                </label>

                <label className="text-sm font-medium text-gray-700">
                  Horário de Fim
                  <input
                    type="time"
                    value={editData.fim}
                    onChange={(e) => setEditData((prev) => ({ ...prev, fim: e.target.value }))}
                    className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    required
                  />
                </label>
              </div>

              <div>
                <span className="block text-sm font-medium text-gray-700 mb-2">Dias de Funcionamento</span>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {DIAS_SEMANA.map((dia) => {
                    const checked = editData.dias.includes(dia.value);
                    return (
                      <label
                        key={dia.value}
                        className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition ${
                          checked
                            ? "border-blue-300 bg-blue-50 text-blue-700"
                            : "border-gray-200 bg-white text-gray-700 hover:border-gray-300"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => handleToggleDay(dia.value)}
                          className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                        />
                        {dia.label}
                      </label>
                    );
                  })}
                </div>
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setIsEditing(false)}
                  className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={savingEdit}
                  className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-70"
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
