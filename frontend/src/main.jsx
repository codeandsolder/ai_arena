import React from 'react'
import ReactDOM from 'react-dom/client'
import { Toaster } from 'react-hot-toast'
import App from './App.jsx'
import './styles/index.css'

// Import highlight.js for C++ syntax highlighting
import hljs from 'highlight.js/lib/core'
import cpp from 'highlight.js/lib/languages/cpp'

// Register C++ language
hljs.registerLanguage('cpp', cpp)

// Make hljs available globally
window.hljs = hljs

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
    <Toaster
      position="top-right"
      toastOptions={{
        duration: 4000,
        style: {
          background: '#1f2937',
          color: '#f3f4f6',
          border: '1px solid #374151',
        },
        success: {
          iconTheme: {
            primary: '#22c55e',
            secondary: '#1f2937',
          },
        },
        error: {
          iconTheme: {
            primary: '#ef4444',
            secondary: '#1f2937',
          },
        },
      }}
    />
  </React.StrictMode>,
)