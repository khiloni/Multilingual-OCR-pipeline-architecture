// Purpose: Entry point rendering the main React application DOM structure.
// Future TODOs: Add ErrorBoundary component wrapper and Sentry performance monitoring setups.

import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
