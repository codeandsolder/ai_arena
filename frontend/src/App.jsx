import { useState, useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Link, NavLink } from 'react-router-dom'
import { Trophy, Code2, Play, Settings, Zap, Loader2 } from 'lucide-react'
import { fetchStatus } from './api'

// Pages
import RunList from './pages/RunList'
import RunConfig from './pages/RunConfig'
import RunDashboard from './pages/RunDashboard'
import RoundDetail from './pages/RoundDetail'
import SolutionDetail from './pages/SolutionDetail'
import ApiCallLog from './pages/ApiCallLog'
import ProblemManager from './pages/ProblemManager'
import SettingsPage from './pages/Settings'

function Layout({ children }) {
  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      {/* Navigation */}
      <nav className="bg-gray-800 border-b border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center">
              <Link to="/" className="flex items-center space-x-2">
                <Zap className="h-8 w-8 text-blue-500" />
                <span className="text-xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
                  AI Optimization Arena
                </span>
              </Link>
            </div>
            <div className="flex items-center space-x-4">
              <NavLink
                to="/"
                className={({ isActive }) =>
                  `flex items-center space-x-1 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-gray-900 text-blue-400'
                      : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                  }`
                }
                end
              >
                <Play className="h-4 w-4" />
                <span>Runs</span>
              </NavLink>
              <NavLink
                to="/problems"
                className={({ isActive }) =>
                  `flex items-center space-x-1 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-gray-900 text-blue-400'
                      : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                  }`
                }
              >
                <Code2 className="h-4 w-4" />
                <span>Problems</span>
              </NavLink>
              <NavLink
                to="/settings"
                className={({ isActive }) =>
                  `flex items-center space-x-1 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-gray-900 text-blue-400'
                      : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                  }`
                }
              >
                <Settings className="h-4 w-4" />
                <span>Settings</span>
              </NavLink>
            </div>
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {children}
      </main>

      {/* Footer */}
      <footer className="bg-gray-800 border-t border-gray-700 mt-auto">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <p className="text-center text-sm text-gray-500">
            AI Optimization Arena - Competitive Programming with AI
          </p>
        </div>
      </footer>
    </div>
  )
}

function App() {
  const [status, setStatus] = useState({ ready: false, message: 'Connecting to backend...' });
  const [error, setError] = useState(null);

  useEffect(() => {
    let interval;
    const checkStatus = async () => {
      try {
        const data = await fetchStatus();
        setStatus(data);
        if (data.ready) {
          clearInterval(interval);
        }
      } catch (err) {
        console.error('Failed to fetch status:', err);
        // Don't show error immediately as backend might still be starting
      }
    };

    checkStatus();
    interval = setInterval(checkStatus, 2000);

    return () => clearInterval(interval);
  }, []);

  if (!status.ready) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center p-4">
        <div className="max-w-md w-full text-center">
          <Zap className="h-16 w-16 text-blue-500 mx-auto mb-6 animate-pulse" />
          <h1 className="text-2xl font-bold text-white mb-2">AI Optimization Arena</h1>
          <div className="bg-gray-800 rounded-lg p-6 shadow-xl border border-gray-700">
            <Loader2 className="h-8 w-8 text-blue-400 mx-auto mb-4 animate-spin" />
            <p className="text-gray-300 text-lg mb-1">{status.message || 'Initializing backend...'}</p>
            <p className="text-gray-500 text-sm">This may take a few minutes if parsing many problems.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<RunList />} />
          <Route path="/runs/new" element={<RunConfig />} />
          <Route path="/runs/:id" element={<RunDashboard />} />
          <Route path="/runs/:id/edit" element={<RunConfig />} />
          <Route path="/runs/:runId/rounds/:roundNumber" element={<RoundDetail />} />
          <Route path="/runs/:runId/rounds/:roundNumber/solutions/:modelSlug" element={<SolutionDetail />} />
          <Route path="/api-calls/:id" element={<ApiCallLog />} />
          <Route path="/problems" element={<ProblemManager />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </Layout>
    </Router>
  )
}

export default App