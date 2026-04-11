import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// StrictMode intentionally removed — it double-invokes effects which
// conflicts with the lightweight-charts DOM lifecycle.
createRoot(document.getElementById('root')).render(<App />)
