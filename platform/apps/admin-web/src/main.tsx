import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { AppProviders } from './app/App';
import { createAppRouter } from './app/router';
import './app/styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('缺少应用挂载节点');

createRoot(root).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={createAppRouter()} />
    </AppProviders>
  </StrictMode>,
);
