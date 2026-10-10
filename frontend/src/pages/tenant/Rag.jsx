import RAGManager from "../../components/RAGManager";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

export default function Rag() {
  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();

  if (!user) return <div className="p-8 text-center text-gray-500">Faça login novamente.</div>;

  return (
    <RAGManager empresaId={empresaId} />
  );
}
