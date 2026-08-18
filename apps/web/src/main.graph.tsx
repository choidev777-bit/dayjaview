import { createRoot } from 'react-dom/client';
import { KnowledgeGraphPage } from './pages/KnowledgeGraphPage';
import './styles/knowledge-graph.css';

const root = document.getElementById('root');
if (!root) throw new Error('지식 그래프 root를 찾을 수 없습니다.');

createRoot(root).render(<KnowledgeGraphPage />);
