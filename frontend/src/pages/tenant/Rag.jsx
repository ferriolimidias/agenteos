import RAGManager from "../../components/RAGManager";

export default function Rag() {
  const user = JSON.parse(localStorage.getItem("user"));
  const empresaId = user?.empresa_id || user?.id; // Fallback pra n quebrar

  if (!user) return <div className="p-8 text-center text-gray-500">Faça login novamente.</div>;

  return (
    <RAGManager empresaId={empresaId} />
  );
}
