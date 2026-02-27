import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Play, Pause, RotateCw, DollarSign, Activity } from 'lucide-react';
import toast from 'react-hot-toast';
import { fetchRun, fetchRounds, startRun, pauseRun, resumeRun, createWebSocket } from '../api';
import StatusBadge from '../components/StatusBadge';
import ScoreBoard from '../components/ScoreBoard';
import BenchmarkChart from '../components/BenchmarkChart';
import RoundTimeline from '../components/RoundTimeline';

function RunDashboard() {
  const { id } = useParams();
  const navigate = useNavigate();
  
  const [run, setRun] = useState(null);
  const [rounds, setRounds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [ws, setWs] = useState(null);
  const [liveLogs, setLiveLogs] = useState([]);

  const loadData = useCallback(async () => {
    try {
      const [runData, roundsData] = await Promise.all([
        fetchRun(id),
        fetchRounds(id),
      ]);
      setRun(runData);
      setRounds(roundsData);
    } catch (error) {
      toast.error('Failed to load run data: ' + error.message);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 3000);
    return () => clearInterval(interval);
  }, [loadData]);

  useEffect(() => {
    if (run?.status === 'running') {
      const websocket = createWebSocket(id, (data) => {
        if (data.type === 'status_update') {
          setLiveLogs(prev => [
            { timestamp: new Date(), message: data.message, model: data.model_slug },
            ...prev.slice(0, 99),
          ]);
        }
      });
      setWs(websocket);

      return () => {
        websocket.close();
      };
    }
  }, [id, run?.status]);

  const handleStart = async () => {
    const rounds = prompt('Number of rounds to run:', '10');
    if (!rounds) return;
    
    try {
      await startRun(id, parseInt(rounds));
      toast.success('Run started');
      loadData();
    } catch (error) {
      toast.error('Failed to start run: ' + error.message);
    }
  };

  const handlePause = async () => {
    try {
      await pauseRun(id);
      toast.success('Run paused');
      loadData();
    } catch (error) {
      toast.error('Failed to pause run: ' + error.message);
    }
  };

  const handleResume = async () => {
    const rounds = prompt('Number of additional rounds:', '10');
    if (!rounds) return;
    
    try {
      await resumeRun(id, parseInt(rounds));
      toast.success('Run resumed');
      loadData();
    } catch (error) {
      toast.error('Failed to resume run: ' + error.message);
    }
  };

  const prepareChartData = (metric) => {
    const data = [];
    rounds.forEach(round => {
      round.solutions?.forEach(solution => {
        let value;
        switch (metric) {
          case 'score':
            value = solution.score || 0;
            break;
          case 'time':
            value = solution.avg_time_ms || 0;
            break;
          case 'memory':
            value = solution.max_memory_kb || 0;
            break;
          default:
            value = 0;
        }
        data.push({
          round: round.round_number,
          model: solution.model_display_name || solution.model_slug,
          value,
        });
      });
    });
    return data;
  };

  const calculateCumulativeStats = () => {
    const stats = {};
    
    rounds.forEach(round => {
      round.solutions?.forEach(solution => {
        const key = solution.model_slug;
        if (!stats[key]) {
          stats[key] = {
            model_slug: solution.model_slug,
            model_display_name: solution.model_display_name || solution.model_slug,
            total_score: 0,
            total_time: 0,
            total_memory: 0,
            tests_passed: 0,
            total_tests: 0,
            rounds_completed: 0,
            status: solution.status,
          };
        }
        
        stats[key].total_score += solution.score || 0;
        stats[key].total_time += solution.avg_time_ms || 0;
        stats[key].total_memory = Math.max(stats[key].total_memory, solution.max_memory_kb || 0);
        stats[key].tests_passed += solution.tests_passed || 0;
        stats[key].total_tests += solution.total_tests || 0;
        stats[key].rounds_completed++;
      });
    });

    return Object.values(stats).map(s => ({
      ...s,
      score: s.total_score,
      avg_time_ms: s.rounds_completed > 0 ? s.total_time / s.rounds_completed : 0,
      max_memory_kb: s.total_memory,
    }));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RotateCw className="h-8 w-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (!run) {
    return <div className="text-center py-12 text-gray-400">Run not found</div>;
  }

  const cumulativeStats = calculateCumulativeStats();
  const totalCost = rounds.reduce((sum, r) => sum + (r.total_cost || 0), 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{run.name}</h1>
          <div className="flex items-center space-x-4 mt-2 text-gray-400">
            <span>Problem: {run.problem?.name || '-'}</span>
            <StatusBadge status={run.status} />
            <span>Rounds: {run.rounds_completed || 0}</span>
          </div>
        </div>
        <div className="flex items-center space-x-3">
          {run.status === 'configured' && (
            <button
              onClick={handleStart}
              className="btn-success flex items-center space-x-2"
            >
              <Play className="h-4 w-4" />
              <span>Start Run</span>
            </button>
          )}
          {run.status === 'running' && (
            <button
              onClick={handlePause}
              className="btn-warning flex items-center space-x-2"
            >
              <Pause className="h-4 w-4" />
              <span>Pause</span>
            </button>
          )}
          {run.status === 'paused' && (
            <button
              onClick={handleResume}
              className="btn-success flex items-center space-x-2"
            >
              <Play className="h-4 w-4" />
              <span>Resume</span>
            </button>
          )}
        </div>
      </div>

      {/* Leaderboard */}
      <div className="card">
        <h2 className="text-lg font-semibold mb-4 flex items-center">
          <Activity className="h-5 w-5 mr-2 text-blue-400" />
          Leaderboard
        </h2>
        {cumulativeStats.length > 0 ? (
          <ScoreBoard solutions={cumulativeStats} showCumulative={true} />
        ) : (
          <p className="text-gray-500 text-center py-8">No data yet</p>
        )}
      </div>

      {/* Rounds Timeline */}
      <div className="card">
        <h2 className="text-lg font-semibold mb-4">Rounds Timeline</h2>
        {rounds.length > 0 ? (
          <RoundTimeline
            rounds={rounds}
            onRoundClick={(round) => navigate(`/runs/${id}/rounds/${round.round_number}`)}
          />
        ) : (
          <p className="text-gray-500 text-center py-8">No rounds completed yet</p>
        )}
      </div>

      {/* Performance Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h3 className="text-md font-semibold mb-4">Score Over Rounds</h3>
          <BenchmarkChart data={prepareChartData('score')} metric="score" />
        </div>
        <div className="card">
          <h3 className="text-md font-semibold mb-4">Average Time Over Rounds</h3>
          <BenchmarkChart data={prepareChartData('time')} metric="time" />
        </div>
        <div className="card lg:col-span-2">
          <h3 className="text-md font-semibold mb-4">Max Memory Over Rounds</h3>
          <BenchmarkChart data={prepareChartData('memory')} metric="memory" />
        </div>
      </div>

      {/* Live Feed */}
      {run.status === 'running' && (
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">Live Feed</h2>
          <div className="h-64 overflow-y-auto bg-gray-950 rounded-lg p-4 font-mono text-sm space-y-1">
            {liveLogs.length === 0 ? (
              <p className="text-gray-500">Waiting for updates...</p>
            ) : (
              liveLogs.map((log, index) => (
                <div key={index} className="flex items-start space-x-2">
                  <span className="text-gray-500 text-xs">
                    {log.timestamp.toLocaleTimeString()}
                  </span>
                  {log.model && (
                    <span className="text-blue-400 text-xs">[{log.model}]</span>
                  )}
                  <span className="text-gray-300">{log.message}</span>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Cost Summary */}
      <div className="card">
        <h2 className="text-lg font-semibold mb-4 flex items-center">
          <DollarSign className="h-5 w-5 mr-2 text-green-400" />
          Cost Summary
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-sm text-gray-400">Total Cost</p>
            <p className="text-2xl font-bold text-green-400">${totalCost.toFixed(4)}</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-sm text-gray-400">Rounds</p>
            <p className="text-2xl font-bold">{rounds.length}</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-sm text-gray-400">Avg Cost/Round</p>
            <p className="text-2xl font-bold">
              ${rounds.length > 0 ? (totalCost / rounds.length).toFixed(4) : '0.0000'}
            </p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-sm text-gray-400">Models</p>
            <p className="text-2xl font-bold">{run.models?.length || 0}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default RunDashboard;