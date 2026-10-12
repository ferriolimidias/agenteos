import "../../styles/ux05-tools.css";
import RAGManager from "../../components/RAGManager";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

export default function Rag() {
  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();

  if (!user) return <div className="ux05-tools-state p-8 text-center text-gray-500">Faça login novamente.</div>;

  return (
    <div className="ux05-tools ux05-rag"><RAGManager empresaId={empresaId} /></div>
  );
}
