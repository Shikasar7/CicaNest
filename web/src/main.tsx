import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { startTheme } from './lib/theme'

// Inside the nest desktop app the window draws Mica behind the page: surfaces turn translucent.
if (window.nestDesktop) document.documentElement.dataset.desktop = 'true'

// Light or dark: the choice in the sidebar, else the system setting.
startTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
