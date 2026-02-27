import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, ChevronDown, ChevronUp, ExternalLink, Terminal } from 'lucide-react';
import toast from 'react-hot-toast';
import { fetchSolution, fetchSolutionTests } from '../api';
import StatusBadge from '../components/StatusBadge';
import CodeViewer from '../components/CodeViewer';

function SolutionDetail() {
  const { runId, roundNumber, modelSlug } = useParams();
  const navigate = useNavigate();
  
  const [solution, setSolution] = useState(null);
  const [tests, setTests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedTests, setExpandedTests] = useState(new Set());
  const [showCompileLog, setShowCompileLog] = useState(false);

  useEffect(() => {
    loadData();
  }, [runId, roundNumber, modelSlug]);

  const loadData = async () => {
    try {
      // Fetch solution - we need to find it by run/round/model
      // For now, assume we can construct the ID or fetch from round
      const solutionId = `${runId}_${roundNumber}_${modelSlug}`;
      const [solutionData, testsData] = await Promise.all([
        fetchSolution(solutionId),
        fetchSolutionTests(solutionId),
      ]);
      setSolution(solutionData);
      setTests(testsData);
    } catch (error) {
      toast.error('Failed to load solution: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const toggleTestExpand = (testIndex) => {
    setExpandedTests(prev => {
      const next = new Set(prev);
      if (next.has(testIndex)) {
        next.delete(testIndex);
      } else {
        next.add(testIndex);
      }
      return next;
    });
  };

  const getVerdictColor = (verdict) => {
    switch (verdict) {
      case 'AC': return 'text-green-400';
      case 'WA': return 'text-red-400';
      case 'TLE': return 'text-yellow-400';
      case 'MLE': return 'text-orange-400';
      case 'RE': return 'text-gray-400';
      default: return 'text-gray-400';
    }
  };

  const truncate = (str, length = 200) => {
    if (!str) return '';
    if (str.length <= length) return str;
    return str.substring(0, length) + '...';
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-400">Loading...</div>;
  }

  if (!solution) {
    return <div className="text-center py-12 text-gray-400">Solution not found</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center space-x-4">
        <button
          onClick={() => navigate(`/runs/${runId}/rounds/${roundNumber}`)}
          className="p-2 hover:bg-gray-800 rounded-lg"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">
            {solution.model_display_name || solution.model_slug}
          </h1>
          <div className="flex items-center space-x-4 mt-1 text-gray-400">
            <StatusBadge status={solution.status} />
            <span>Score: <span className="text-white font-mono">{solution.score?.toFixed(2) || 0}</span></span>
            <span>Round {roundNumber}</span>
          </div>
        </div>
      </div>

      {/* Code Section */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold flex items-center">
            <Terminal className="h-5 w-5 mr-2 text-blue-400" />
            Solution Code
          </h2>
          <div className="text-sm text-gray-400">
            {solution.compiler && (
              <span className="font-mono">{solution.compiler} {solution.compiler_flags}</span>
            )}
          </div>
        </div>
        <CodeViewer code={solution.code || '// No code available'} language="cpp" showLineNumbers={true} />
      </div>

      {/* Compilation Section */}
      {solution.compile_log && (
        <div className="card">
          <button
            onClick={() => setShowCompileLog(!showCompileLog)}
            className="w-full flex items-center justify-between"
          >
            <div className="flex items-center space-x-2">
              <h2 className="text-lg font-semibold">Compilation</h2>
              <StatusBadge 
                status={solution.compile_success ? 'completed' : 'error'} 
                size="sm" 
                customLabel={solution.compile_success ? 'Success' : 'Failed'}
              />
            </div>
            {showCompileLog ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
          </button>
          {showCompileLog && (
            <pre className="mt-4 p-4 bg-gray-950 rounded-lg text-sm font-mono text-gray-300 overflow-x-auto">
              {solution.compile_log}
            </pre>
          )}
        </div>
      )}

      {/* Model's Explanation */}
      {solution.explanation && (
        <div className="card">
          <h2 className="text-lg font-semibold mb-3">Model's Explanation</h2>
          <div className="prose prose-invert prose-sm max-w-none">
            <p className="text-gray-300 whitespace-pre-wrap">{solution.explanation}</p>
          </div>
        </div>
      )}

      {/* Test Results */}
      <div className="card">
        <h2 className="text-lg font-semibold mb-4">
          Test Results ({solution.tests_passed || 0}/{solution.total_tests || 0} passed)
        </h2>
        {tests.length === 0 ? (
          <p className="text-gray-500">No test results available</p>
        ) : (
          <div className="space-y-2">
            {tests.map((test, index) => (
              <div key={index} className="border border-gray-700 rounded-lg overflow-hidden">
                <button
                  onClick={() => toggleTestExpand(index)}
                  className="w-full flex items-center justify-between p-3 bg-gray-800 hover:bg-gray-750"
                >
                  <div className="flex items-center space-x-4">
                    <span className="font-mono text-sm text-gray-400">Test {test.test_index}</span>
                    <span className={`font-mono font-bold ${getVerdictColor(test.verdict)}`}>
                      {test.verdict}
                    </span>
                  </div>
                  <div className="flex items-center space-x-4 text-sm text-gray-400">
                    <span>{test.time_ms?.toFixed(2)} ms</span>
                    <span>{test.memory_kb ? `${(test.memory_kb / 1024).toFixed(2)} MB` : '-'}</span>
                    {test.verdict !== 'AC' && (
                      expandedTests.has(index) ? 
                        <ChevronUp className="h-4 w-4" /> : 
                        <ChevronDown className="h-4 w-4" />
                    )}
                  </div>
                </button>
                {expandedTests.has(index) && test.verdict !== 'AC' && (
                  <div className="p-4 bg-gray-950 grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <p className="text-gray-500 mb-1">Expected Output:</p>
                      <pre className="bg-gray-900 p-2 rounded text-gray-300 overflow-x-auto">
                        {truncate(test.expected_output, 500)}
                      </pre>
                    </div>
                    <div>
                      <p className="text-gray-500 mb-1">Actual Output:</p>
                      <pre className="bg-gray-900 p-2 rounded text-gray-300 overflow-x-auto">
                        {truncate(test.actual_output, 500)}
                      </pre>
                    </div>
                    {test.error_message && (
                      <div className="col-span-2">
                        <p className="text-gray-500 mb-1">Error:</p>
                        <pre className="bg-red-900/20 border border-red-800 p-2 rounded text-red-300">
                          {test.error_message}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* API Calls */}
      {solution.api_calls?.length > 0 && (
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">API Calls</h2>
          <div className="space-y-2">
            {solution.api_calls.map((call) => (
              <button
                key={call.id}
                onClick={() => navigate(`/api-calls/${call.id}`)}
                className="w-full flex items-center justify-between p-3 bg-gray-800 hover:bg-gray-750 rounded-lg text-left"
              >
                <div className="flex items-center space-x-3">
                  <span className="text-sm text-gray-400">{call.purpose}</span>
                  <span className="text-xs text-gray-500">{call.model}</span>
                </div>
                <div className="flex items-center space-x-3 text-sm">
                  <span className="text-gray-400">${call.cost?.toFixed(4) || 0}</span>
                  <span className="text-gray-500">{call.latency_ms}ms</span>
                  <ExternalLink className="h-4 w-4 text-gray-500" />
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default SolutionDetail;