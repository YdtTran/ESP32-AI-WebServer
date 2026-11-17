import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConsoleAuth } from './components/ConsoleAuth';
import { ToastProvider } from './components/Toast';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ToastProvider>
      <ConsoleAuth />
    </ToastProvider>
  </React.StrictMode>
);
