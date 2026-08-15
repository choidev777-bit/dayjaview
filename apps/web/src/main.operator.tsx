import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { OperatorConsole } from './operator/OperatorConsole';
import { createOperatorRepository } from './operator/operatorRepository';
import './styles/tokens.css';
import './operator/operator.css';

const root = document.getElementById('root');
if (!root) throw new Error('운영자 콘솔 root를 찾을 수 없습니다.');

createRoot(root).render(
  <StrictMode>
    <OperatorConsole
      repository={createOperatorRepository()}
      currentPath={window.location.pathname}
    />
  </StrictMode>,
);
