import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Eye, Trash2, Play, Pause, RotateCw } from 'lucide-react';
import { format } from 'date-fns';
import toast from 'react-hot-toast';
import { fetchRuns, deleteRun, startRun, pauseRun, resumeRun } from '../api';
import StatusBadge from '../components/StatusBadge';

function RunList() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const loadRuns = async () => {
    try {
      const data = await fetchRuns();
      setRuns(data);
    } catch (error) {
      toast.error('Failed to load runs: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRuns();
    
    // Auto-refresh every 5 seconds
    const interval = setInterval(loadRuns, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleDelete = async (id) => {
    if (!window.confirm('Are you sure you want to delete this run?')) return;
    
    try {
      await deleteRun(id);
      toast.success('Run deleted successfully');
      loadRuns();
    } catch (error) {
      toast.error('Failed to delete run: ' + error.message);
    }
  };

  const handleStart = async (id) => {
    const rounds = prompt('Number of rounds to run:', '10');
    if (!rounds) return;
    
    try {
      await startRun(id, parseInt(rounds));
      toast.success('Run started');
      loadRuns();
    } catch (error) {
      toast.error('Failed to start run: ' + error.message);
    }
  };

  const handlePause = async (id) => {
    try {
      await pauseRun(id);
      toast.success('Run paused');
      loadRuns();
    } catch (error) {
      toast.error('Failed to pause run: ' + error.message);
    }
  };

  const handleResume = async (id) => {
    const rounds = prompt('Number of additional rounds:', '10');
    if (!rounds) return;
    
    try {
      await resumeRun(id, parseInt(rounds));
      toast.success('Run resumed');
      loadRuns();
    } catch (error) {
      toast.error('Failed to resume run: ' + error.message);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RotateCw className="h-8 w-8 animate-spin text-blue-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Runs</h1>
          <p className="text-gray-400 mt-1">Manage and monitor AI optimization runs</p>
        </div>
        <button
          onClick={() => navigate('/runs/new')}
          className="btn-primary flex items-center space-x-2"
        >
          <Plus className="h-4 w-4" />
          <span>New Run</span>
        </button>
      </div>

      {runs.length === 0 ? (
        <div className="card text-center py-12">
          <div className="text-gray-500 mb-4">
            <Play className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p className="text-lg">No runs yet</p>
            <p className="text-sm mt-1">Create your first run to start optimizing</p>
          </div>
          <button
            onClick={() => navigate('/runs/new')}
            className="btn-primary"
          >
            Create Run
          </button>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Problem</th>
                <th>Status</th>
                <th>Rounds</th>
                <th>Models</th>
                <th>Created</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="group">
                  <td className="font-medium">{run.name}</td>
                  <td className="text-gray-400">{run.problem?.name || '-'}</td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="font-mono">{run.rounds_completed || 0}</td>
                  <td className="font-mono">{run.models?.length || 0}</td>
                  <td className="text-gray-400">
                    {run.created_at ? format(new Date(run.created_at), 'MMM d, yyyy HH:mm') : '-'}
                  </td>
                  <td>
                    <div className="flex items-center justify-end space-x-2">
                      {run.status === 'configured' && (
                        <button
                          onClick={() => handleStart(run.id)}
                          className="p-2 text-green-400 hover:bg-green-400/10 rounded-lg"
                          title="Start Run"
                        >
                          <Play className="h-4 w-4" />
                        </button>
                      )}
                      {run.status === 'running' && (
                        <button
                          onClick={() => handlePause(run.id)}
                          className="p-2 text-yellow-400 hover:bg-yellow-400/10 rounded-lg"
                          title="Pause Run"
                        >
                          <Pause className="h-4 w-4" />
                        </button>
                      )}
                      {run.status === 'paused' && (
                        <button
                          onClick={() => handleResume(run.id)}
                          className="p-2 text-green-400 hover:bg-green-400/10 rounded-lg"
                          title="Resume Run"
                        >
                          <Play className="h-4 w-4" />
                        </button>
                      )}
                      <button
                        onClick={() => navigate(`/runs/${run.id}`)}
                        className="p-2 text-blue-400 hover:bg-blue-400/10 rounded-lg"
                        title="View Details"
                      >
                        <Eye className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => navigate(`/runs/${run.id}/edit`)}
                        className="p-2 text-gray-400 hover:bg-gray-700 rounded-lg"
                        title="Edit"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                        </svg>
                      </button>
                      <button
                        onClick={() => handleDelete(run.id)}
                        className="p-2 text-red-400 hover:bg-red-400/10 rounded-lg"
                        title="Delete"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default RunList;