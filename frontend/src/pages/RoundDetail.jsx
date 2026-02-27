import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Code2, Clock, MemoryStick, CheckCircle, AlertTriangle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import toast from 'react-hot-toast';
import { fetchRound, fetchRun } from '../api';
import StatusBadge from '../components/StatusBadge';

function RoundDetail() {
  const { runId, roundNumber } = useParams();
  const navigate = useNavigate();
  
  const [round, setRound] = useState(null);
  const [run, setRun] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, [runId, roundNumber]);

  const loadData = async () => {
    try {
      const [roundData, runData] = await Promise.all([
        fetchRound(`${runId}_${roundNumber}`),
        fetchRun(runId),
      ]);
      setRound(roundData);
      setRun(runData);
    } catch (error) {
      toast.error('Failed to load round data: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const getVerdictSummary = (solution) => {
    if (!solution.test_results) return '-';
    
    const verdicts = {};
    solution.test_results.forEach(t => {
      verdicts[t.verdict] = (verdicts[t.verdict] || 0) + 1;
    });
    
    const parts = [];
    if (verdicts.AC) parts.push(`${verdicts.AC} AC`);
    if (verdicts.WA) parts.push(`${verdicts.WA} WA`);
    if (verdicts.TLE) parts.push(`${verdicts.TLE} TLE`);
    if (verdicts.MLE) parts.push(`${verdicts.MLE} MLE`);
    if (verdicts.RE) parts.push(`${verdicts.RE} RE`);
    
    return parts.join(', ') || 'No tests';
  };

  const getScoreColor = (score) => {
    if (score >= 80) return 'text-green-400';
    if (score >= 50) return 'text-yellow-400';
    if (score > 0) return 'text-orange-400';
    return 'text-red-400';
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-400">Loading...</div>;
  }

  if (!round) {
    return <div className="text-center py-12 text-gray-400">Round not found</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center space-x-4">
        <button
          onClick={() => navigate(`/runs/${runId}`)}
          className="p-2 hover:bg-gray-800 rounded-lg"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">
            Round {round.round_number} - {run?.name}
          </h1>
          <div className="flex items-center space-x-4 mt-1 text-gray-400">
            <StatusBadge status={round.status} />
            <span>{round.solutions?.length || 0} models</span>
          </div>
        </div>
      </div>

      {/* Summary */}
      {round.summary && (
        <div className="card">
          <h2 className="text-lg font-semibold mb-3">Round Summary</h2>
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{round.summary}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Solutions Grid */}
      <div>
        <h2 className="text-lg font-semibold mb-4">Solutions</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {round.solutions?.map((solution) => (
            <button
              key={solution.id}
              onClick={() => navigate(`/runs/${runId}/rounds/${roundNumber}/solutions/${solution.model_slug}`)}
              className="card text-left hover:border-blue-500 transition-colors group"
            >
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="font-semibold text-lg group-hover:text-blue-400">
                    {solution.model_display_name || solution.model_slug}
                  </h3>
                  <StatusBadge status={solution.status} size="sm" />
                </div>
                <div className={`text-2xl font-bold ${getScoreColor(solution.score)}`}>
                  {solution.score?.toFixed(1) || 0}
                </div>
              </div>

              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-gray-400 flex items-center">
                    <CheckCircle className="h-4 w-4 mr-1" />
                    Tests
                  </span>
                  <span className={solution.tests_passed === solution.total_tests ? 'text-green-400' : 'text-yellow-400'}>
                    {solution.tests_passed || 0}/{solution.total_tests || 0}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-gray-400 flex items-center">
                    <Clock className="h-4 w-4 mr-1" />
                    Avg Time
                  </span>
                  <span className="font-mono">
                    {solution.avg_time_ms ? `${solution.avg_time_ms.toFixed(2)} ms` : '-'}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-gray-400 flex items-center">
                    <MemoryStick className="h-4 w-4 mr-1" />
                    Max Memory
                  </span>
                  <span className="font-mono">
                    {solution.max_memory_kb ? `${(solution.max_memory_kb / 1024).toFixed(2)} MB` : '-'}
                  </span>
                </div>

                <div className="pt-2 border-t border-gray-700">
                  <span className="text-gray-400 text-xs">Verdicts:</span>
                  <p className="text-xs mt-1">{getVerdictSummary(solution)}</p>
                </div>

                {solution.compiler && (
                  <div className="pt-1">
                    <span className="text-gray-400 text-xs">Compiler:</span>
                    <p className="text-xs mt-1 font-mono text-gray-300">
                      {solution.compiler} {solution.compiler_flags}
                    </p>
                  </div>
                )}

                {solution.has_compile_errors && (
                  <div className="flex items-center text-red-400 text-xs mt-2">
                    <AlertTriangle className="h-4 w-4 mr-1" />
                    Compilation errors
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export default RoundDetail;