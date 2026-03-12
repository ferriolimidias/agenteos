import { useState, useEffect } from "react";
import api from "../services/api";
import { Brain, Link as LinkIcon, FileText, Plus, Trash2, CheckCircle2 } from "lucide-react";

export default function RAGManager({ empresaId }) {
  const [conhecimentos, setConhecimentos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState("texto"); // 'texto' ou 'url' ou 'pdf'
  
  // Form State
  const [conteudo, setConteudo] = useState("");
  const [pdfFile, setPdfFile] = useState(null);
  const [successMsg, setSuccessMsg] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  const fetchConhecimentos = async () => {
    try {
      setLoading(true);
      const res = await api.get(`/empresas/${empresaId}/rag`);
      setConhecimentos(res.data);
    } catch (err) {
      console.error("Erro ao buscar conhecimento RAG:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (empresaId) fetchConhecimentos();
    // eslint-disable-next-line
  }, [empresaId]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (activeTab !== "pdf" && !conteudo.trim()) return;
    if (activeTab === "pdf" && !pdfFile) return;
    
    setSubmitting(true);
    setErrorMsg("");
    setSuccessMsg("");

    try {
      if (activeTab === "pdf") {
        const formData = new FormData();
        formData.append("file", pdfFile);
        
        await api.post(`/empresas/${empresaId}/rag/pdf`, formData, {
          headers: {
            'Content-Type': 'multipart/form-data'
          }
        });
        setPdfFile(null);
      } else {
        await api.post(`/empresas/${empresaId}/rag`, {
          tipo: activeTab,
          conteudo: conteudo
        });
        setConteudo("");
      }

      setSuccessMsg("Base de conhecimento atualizada com sucesso!");
      fetchConhecimentos();
      
      // Limpa mensagem de sucesso depois de 3 seg
      setTimeout(() => setSuccessMsg(""), 3000);
    } catch (err) {
      setErrorMsg("Erro ao adicionar conhecimento. Tente novamente.");
    } finally {
      setSubmitting(false);
    }
  };

  if (!empresaId) return <div className="p-8 text-center text-gray-500">ID da empresa não fornecido.</div>;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 tracking-tight flex items-center gap-3">
          <Brain className="text-blue-600" size={32} />
          Treinamento de IA (RAG)
        </h1>
        <p className="text-gray-500 mt-1">
          Adicione textos, manuais ou links para ensinar o Agente IA sobre a empresa.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Coluna Esquerda: Form de Inserção */}
        <div className="lg:col-span-1 space-y-4">
          <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
            <div className="bg-gray-50 px-6 py-4 border-b border-gray-100">
              <h2 className="text-lg font-semibold text-gray-800">Adicionar Conhecimento</h2>
            </div>
            
            <div className="p-6">
              {/* Tabs */}
              <div className="flex space-x-2 mb-6 bg-gray-100 p-1 rounded-lg">
                <button
                  type="button"
                  onClick={() => { setActiveTab("texto"); setConteudo(""); setPdfFile(null); }}
                  className={`flex-1 py-2 px-3 rounded-md text-sm font-medium transition-all flex items-center justify-center gap-2 ${activeTab === 'texto' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  <FileText size={16} /> Texto
                </button>
                <button
                  type="button"
                  onClick={() => { setActiveTab("url"); setConteudo(""); setPdfFile(null); }}
                  className={`flex-1 py-2 px-3 rounded-md text-sm font-medium transition-all flex items-center justify-center gap-2 ${activeTab === 'url' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  <LinkIcon size={16} /> Link / URL
                </button>
                <button
                  type="button"
                  onClick={() => { setActiveTab("pdf"); setConteudo(""); setPdfFile(null); }}
                  className={`flex-1 py-2 px-3 rounded-md text-sm font-medium transition-all flex items-center justify-center gap-2 ${activeTab === 'pdf' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  <FileText size={16} /> PDF
                </button>
              </div>

              {successMsg && (
                <div className="mb-4 p-3 bg-green-50 text-green-700 text-sm rounded-lg flex items-center gap-2">
                  <CheckCircle2 size={16} /> {successMsg}
                </div>
              )}

              {errorMsg && (
                <div className="mb-4 p-3 bg-red-50 text-red-600 text-sm rounded-lg">
                  {errorMsg}
                </div>
              )}

              <form onSubmit={handleSubmit} className="space-y-4">
                {activeTab === "texto" ? (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Conteúdo em Texto Misto
                    </label>
                    <textarea 
                      required
                      rows={6}
                      value={conteudo}
                      onChange={(e) => setConteudo(e.target.value)}
                      placeholder="Cole aqui regras de negócio, scripts de vendas, FAQ..."
                      className="w-full border border-gray-300 rounded-lg py-2 px-3 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all resize-none text-sm"
                    />
                  </div>
                ) : activeTab === "url" ? (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      URL Pública do Site/Artigo
                    </label>
                    <input 
                      type="url"
                      required
                      value={conteudo}
                      onChange={(e) => setConteudo(e.target.value)}
                      placeholder="https://suaempresa.com.br/faq"
                      className="w-full border border-gray-300 rounded-lg py-2 px-3 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all text-sm"
                    />
                  </div>
                ) : (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Selecione o Arquivo PDF
                    </label>
                    <input 
                      type="file"
                      accept="application/pdf"
                      required
                      onChange={(e) => setPdfFile(e.target.files[0])}
                      className="w-full border border-gray-300 rounded-lg py-2 px-3 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all text-sm file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
                    />
                  </div>
                )}
                
                <button 
                  type="submit"
                  disabled={submitting || (activeTab !== "pdf" && !conteudo.trim()) || (activeTab === "pdf" && !pdfFile)}
                  className="w-full bg-blue-600 hover:bg-blue-700 text-white py-2.5 rounded-lg flex items-center justify-center gap-2 font-medium transition-colors shadow-sm disabled:opacity-70 disabled:cursor-not-allowed"
                >
                  <Plus size={18} />
                  {submitting ? "Processando..." : "Treinar IA"}
                </button>
              </form>
            </div>
          </div>
        </div>

        {/* Coluna Direita: Lista de Conhecimentos */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden h-full flex flex-col">
            <div className="bg-gray-50 px-6 py-4 border-b border-gray-100 flex justify-between items-center">
              <h2 className="text-lg font-semibold text-gray-800">Base de Conhecimento Ativa</h2>
              <span className="bg-blue-100 text-blue-700 text-xs font-bold px-2 py-1 rounded-full">
                {conhecimentos.length} itens
              </span>
            </div>
            
            <div className="flex-1 p-0 overflow-y-auto max-h-[600px] custom-scrollbar">
              {loading ? (
                <div className="p-8 flex justify-center items-center text-gray-400">
                  <div className="animate-pulse flex flex-col items-center">
                    <Brain className="mb-2 opacity-50" size={32} />
                    <span>Carregando memória da IA...</span>
                  </div>
                </div>
              ) : conhecimentos.length === 0 ? (
                <div className="p-12 text-center flex flex-col items-center justify-center text-gray-500 h-full">
                  <div className="w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center mb-4">
                    <Brain size={32} className="text-gray-400" />
                  </div>
                  <h3 className="text-lg font-medium text-gray-900 mb-1">Cérebro Vazio</h3>
                  <p className="max-w-xs text-sm">A base de conhecimento está vazia. Adicione textos ou URLs para ensinar o agente.</p>
                </div>
              ) : (
                <ul className="divide-y divide-gray-100">
                  {conhecimentos.map((item) => (
                    <li key={item.id} className="p-6 hover:bg-gray-50 transition-colors flex items-start gap-4 group">
                      <div className={`mt-1 p-2 rounded-lg flex-shrink-0 ${item.tipo === 'url' ? 'bg-indigo-50 text-indigo-600' : 'bg-blue-50 text-blue-600'}`}>
                        {item.tipo === 'url' ? <LinkIcon size={20} /> : <FileText size={20} />}
                      </div>
                      
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-medium uppercase tracking-wider text-gray-500">
                            {item.tipo === 'url' ? 'Link Web' : item.tipo === 'pdf' ? 'Arquivo PDF' : 'Texto Puro'}
                          </span>
                          <span className="text-xs font-medium bg-green-100 text-green-700 px-2 py-0.5 rounded-full inline-flex items-center gap-1">
                            <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                            {item.status}
                          </span>
                        </div>
                        
                        <p className={`text-sm text-gray-800 break-words ${item.tipo === 'texto' ? 'line-clamp-3' : 'font-mono text-blue-600 hover:underline cursor-pointer'}`}>
                          {item.conteudo}
                        </p>
                      </div>
                      
                      <button className="text-gray-300 hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100 p-2">
                        <Trash2 size={18} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
