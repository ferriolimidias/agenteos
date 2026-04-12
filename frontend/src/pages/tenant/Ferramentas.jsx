import { useEffect, useMemo, useState } from "react";
import { Edit2, Loader2, Plus, Trash2, X } from "lucide-react";

const EMPTY_FORM = {
  id: null,
  nome: "",
  descricao_ia: "",
  endpoint: "",
  metodo: "GET",
  headers_json: "{}",
  schema_parametros: "",
};

const EXEMPLO_SCHEMA = `{
  "type": "object",
  "properties": {
    "cep": {
      "type": "string"
    }
  }
}`;

const MOCK_TOOLS = [
  {
    id: "tool-1",
    nome: "Consulta CEP",
    descricao_ia: "Use para consultar endereço completo a partir do CEP informado pelo cliente.",
    endpoint: "https://api.exemplo.com/cep/consultar",
    metodo: "GET",
    headers_json: '{"Authorization":"Bearer token"}',
    schema_parametros: EXEMPLO_SCHEMA,
  },
];

const METHOD_COLORS = {
  GET: "bg-emerald-100 text-emerald-700",
  POST: "bg-blue-100 text-blue-700",
  PUT: "bg-amber-100 text-amber-700",
  DELETE: "bg-rose-100 text-rose-700",
  PATCH: "bg-violet-100 text-violet-700",
};

export default function Ferramentas() {
  const [ferramentas, setFerramentas] = useState([]);
  const [formData, setFormData] = useState(EMPTY_FORM);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [erroForm, setErroForm] = useState("");

  // Mock de GET
  const fetchTools = async () => {
    setIsLoading(true);
    await new Promise((resolve) => setTimeout(resolve, 300));
    setFerramentas(MOCK_TOOLS);
    setIsLoading(false);
  };

  // Mock de POST/PUT
  const saveTool = async (payload) => {
    await new Promise((resolve) => setTimeout(resolve, 450));
    setFerramentas((prev) => {
      if (payload.id) {
        return prev.map((item) => (item.id === payload.id ? payload : item));
      }
      return [{ ...payload, id: `tool-${Date.now()}` }, ...prev];
    });
  };

  // Mock de DELETE
  const deleteTool = async (toolId) => {
    await new Promise((resolve) => setTimeout(resolve, 250));
    setFerramentas((prev) => prev.filter((item) => item.id !== toolId));
  };

  useEffect(() => {
    fetchTools();
  }, []);

  const totalIntegracoes = useMemo(() => ferramentas.length, [ferramentas]);

  const abrirNovo = () => {
    setErroForm("");
    setFormData(EMPTY_FORM);
    setIsModalOpen(true);
  };

  const abrirEdicao = (tool) => {
    setErroForm("");
    setFormData({
      id: tool.id,
      nome: tool.nome || "",
      descricao_ia: tool.descricao_ia || "",
      endpoint: tool.endpoint || "",
      metodo: tool.metodo || "GET",
      headers_json: tool.headers_json || "{}",
      schema_parametros: tool.schema_parametros || "",
    });
    setIsModalOpen(true);
  };

  const handleExcluir = async (toolId) => {
    if (!window.confirm("Deseja excluir esta integração?")) return;
    await deleteTool(toolId);
  };

  const handleChange = (field, value) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setErroForm("");

    if (!formData.nome.trim() || !formData.descricao_ia.trim() || !formData.endpoint.trim()) {
      setErroForm("Preencha os campos obrigatórios: Nome, Descrição da IA e Endpoint.");
      return;
    }

    try {
      JSON.parse(formData.headers_json || "{}");
    } catch (_) {
      setErroForm("Headers (JSON) inválido.");
      return;
    }

    try {
      JSON.parse(formData.schema_parametros || "{}");
    } catch (_) {
      setErroForm("Schema de Parâmetros (JSON) inválido.");
      return;
    }

    setIsSubmitting(true);
    await saveTool({
      ...formData,
      nome: formData.nome.trim(),
      descricao_ia: formData.descricao_ia.trim(),
      endpoint: formData.endpoint.trim(),
    });
    setIsSubmitting(false);
    setIsModalOpen(false);
    setFormData(EMPTY_FORM);
  };

  return (
    <div className="min-h-full bg-gray-50 p-6">
      <div className="mx-auto max-w-7xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Integrações & APIs</h1>
            <p className="mt-1 text-sm text-gray-500">{totalIntegracoes} integração(ões) cadastrada(s).</p>
          </div>
          <button
            type="button"
            onClick={abrirNovo}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-blue-700"
          >
            <Plus size={16} />
            Nova Integração
          </button>
        </div>

        {isLoading ? (
          <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 shadow-sm">
            Carregando integrações...
          </div>
        ) : ferramentas.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-gray-300 bg-white p-10 text-center text-gray-500 shadow-sm">
            Nenhuma integração cadastrada.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {ferramentas.map((tool) => (
              <div key={tool.id} className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                <div className="mb-3 flex items-start justify-between gap-3">
                  <h3 className="line-clamp-1 text-base font-semibold text-gray-900">{tool.nome}</h3>
                  <span
                    className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                      METHOD_COLORS[tool.metodo] || "bg-gray-100 text-gray-700"
                    }`}
                  >
                    {tool.metodo}
                  </span>
                </div>

                <p className="mb-2 truncate text-xs text-gray-500">{tool.endpoint}</p>
                <p className="line-clamp-3 min-h-[60px] text-sm text-gray-700">{tool.descricao_ia}</p>

                <div className="mt-4 flex items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => abrirEdicao(tool)}
                    className="rounded-lg border border-gray-200 p-2 text-gray-600 transition hover:bg-gray-50"
                    title="Editar"
                  >
                    <Edit2 size={15} />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleExcluir(tool.id)}
                    className="rounded-lg border border-red-200 p-2 text-red-600 transition hover:bg-red-50"
                    title="Excluir"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {isModalOpen ? (
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/30" onClick={() => !isSubmitting && setIsModalOpen(false)} />

          <aside className="absolute right-0 top-0 h-full w-full max-w-2xl bg-white shadow-2xl">
            <form onSubmit={handleSubmit} className="flex h-full flex-col">
              <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
                <h2 className="text-lg font-semibold text-gray-900">
                  {formData.id ? "Editar Integração" : "Nova Integração"}
                </h2>
                <button
                  type="button"
                  onClick={() => !isSubmitting && setIsModalOpen(false)}
                  className="rounded-lg p-2 text-gray-500 hover:bg-gray-100"
                >
                  <X size={18} />
                </button>
              </div>

              <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Nome da Conexão *</label>
                  <input
                    value={formData.nome}
                    onChange={(e) => handleChange("nome", e.target.value)}
                    className="w-full rounded-xl border border-gray-300 px-3 py-2.5 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    placeholder="Ex: API de Frete"
                    required
                  />
                </div>

                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Descrição da IA *</label>
                  <textarea
                    value={formData.descricao_ia}
                    onChange={(e) => handleChange("descricao_ia", e.target.value)}
                    rows={4}
                    className="w-full rounded-xl border border-gray-300 px-3 py-2.5 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    placeholder="Como a IA deve entender o uso desta ferramenta. Ex: Use para consultar o preço de frete."
                    required
                  />
                </div>

                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                  <div className="md:col-span-2">
                    <label className="mb-1 block text-sm font-medium text-gray-700">URL Base / Endpoint *</label>
                    <input
                      value={formData.endpoint}
                      onChange={(e) => handleChange("endpoint", e.target.value)}
                      className="w-full rounded-xl border border-gray-300 px-3 py-2.5 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                      placeholder="https://api.exemplo.com/v1/frete"
                      required
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">Método HTTP</label>
                    <select
                      value={formData.metodo}
                      onChange={(e) => handleChange("metodo", e.target.value)}
                      className="w-full rounded-xl border border-gray-300 px-3 py-2.5 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    >
                      {["GET", "POST", "PUT", "DELETE", "PATCH"].map((method) => (
                        <option key={method} value={method}>
                          {method}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Headers (JSON)</label>
                  <textarea
                    value={formData.headers_json}
                    onChange={(e) => handleChange("headers_json", e.target.value)}
                    rows={4}
                    className="w-full rounded-xl border border-gray-300 px-3 py-2.5 font-mono text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    placeholder='{"Authorization": "Bearer..."}'
                  />
                </div>

                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label className="block text-sm font-medium text-gray-700">Schema de Parâmetros (JSON)</label>
                    <button
                      type="button"
                      onClick={() => handleChange("schema_parametros", EXEMPLO_SCHEMA)}
                      className="rounded-lg border border-gray-200 px-2.5 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50"
                    >
                      Inserir Exemplo
                    </button>
                  </div>
                  <textarea
                    value={formData.schema_parametros}
                    onChange={(e) => handleChange("schema_parametros", e.target.value)}
                    rows={10}
                    className="w-full rounded-xl border border-gray-300 px-3 py-2.5 font-mono text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    placeholder='{"type":"object","properties":{"cep":{"type":"string"}}}'
                  />
                </div>

                {erroForm ? (
                  <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {erroForm}
                  </div>
                ) : null}
              </div>

              <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-6 py-4">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                  disabled={isSubmitting}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-70"
                >
                  {isSubmitting ? <Loader2 size={15} className="animate-spin" /> : null}
                  {isSubmitting ? "Salvando..." : "Salvar"}
                </button>
              </div>
            </form>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
