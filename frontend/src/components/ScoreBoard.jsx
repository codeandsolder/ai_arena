import React, { useState } from 'react';
import { ArrowUpDown, TrendingUp, Clock, MemoryStick } from 'lucide-react';
import StatusBadge from './StatusBadge';

function ScoreBoard({ solutions, showCumulative = true }) {
  const [sortField, setSortField] = useState('score');
  const [sortDirection, setSortDirection] = useState('desc');

  const handleSort = (field) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  const sortedSolutions = [...solutions].sort((a, b) => {
    let aVal, bVal;
    
    switch (sortField) {
      case 'model':
        aVal = a.model_slug;
        bVal = b.model_slug;
        break;
      case 'score':
        aVal = a.total_score || a.score || 0;
        bVal = b.total_score || b.score || 0;
        break;
      case 'time':
        aVal = a.avg_time_ms || 0;
        bVal = b.avg_time_ms || 0;
        break;
      case 'memory':
        aVal = a.max_memory_kb || 0;
        bVal = b.max_memory_kb || 0;
        break;
      case 'tests':
        aVal = a.tests_passed || 0;
        bVal = b.tests_passed || 0;
        break;
      default:
        aVal = a[sortField] || 0;
        bVal = b[sortField] || 0;
    }

    if (sortDirection === 'asc') {
      return aVal > bVal ? 1 : -1;
    }
    return aVal < bVal ? 1 : -1;
  });

  const getSortIcon = (field) => {
    if (sortField !== field) return <ArrowUpDown className="h-4 w-4 text-gray-500" />;
    return <ArrowUpDown className={`h-4 w-4 ${sortDirection === 'asc' ? 'rotate-180' : ''} text-blue-400`} />;
  };

  const formatScore = (score) => {
    if (score === undefined || score === null) return '-';
    return score.toFixed(2);
  };

  return (
    <div className="table-container">
      <table className="table">
        <thead>
          <tr>
            <th className="w-12 text-center">#</th>
            <th 
              className="cursor-pointer hover:text-white"
              onClick={() => handleSort('model')}
            >
              <div className="flex items-center space-x-1">
                <span>Model</span>
                {getSortIcon('model')}
              </div>
            </th>
            <th 
              className="cursor-pointer hover:text-white"
              onClick={() => handleSort('score')}
            >
              <div className="flex items-center space-x-1">
                <TrendingUp className="h-4 w-4" />
                <span>Score</span>
                {getSortIcon('score')}
              </div>
            </th>
            {showCumulative && (
              <>
                <th 
                  className="cursor-pointer hover:text-white"
                  onClick={() => handleSort('time')}
                >
                  <div className="flex items-center space-x-1">
                    <Clock className="h-4 w-4" />
                    <span>Avg Time</span>
                    {getSortIcon('time')}
                  </div>
                </th>
                <th 
                  className="cursor-pointer hover:text-white"
                  onClick={() => handleSort('memory')}
                >
                  <div className="flex items-center space-x-1">
                    <MemoryStick className="h-4 w-4" />
                    <span>Max Memory</span>
                    {getSortIcon('memory')}
                  </div>
                </th>
                <th 
                  className="cursor-pointer hover:text-white"
                  onClick={() => handleSort('tests')}
                >
                  <div className="flex items-center space-x-1">
                    <span>Tests</span>
                    {getSortIcon('tests')}
                  </div>
                </th>
              </>
            )}
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {sortedSolutions.map((solution, index) => (
            <tr key={solution.id || index} className="group">
              <td className="text-center font-mono text-gray-500">
                {index + 1}
              </td>
              <td className="font-medium">
                {solution.model_display_name || solution.model_slug}
              </td>
              <td>
                <span className={`font-mono font-bold ${
                  (solution.total_score || solution.score || 0) > 0 
                    ? 'text-green-400' 
                    : 'text-gray-400'
                }`}>
                  {formatScore(solution.total_score || solution.score)}
                </span>
              </td>
              {showCumulative && (
                <>
                  <td className="font-mono">
                    {solution.avg_time_ms ? `${(solution.avg_time_ms).toFixed(2)} ms` : '-'}
                  </td>
                  <td className="font-mono">
                    {solution.max_memory_kb ? `${(solution.max_memory_kb / 1024).toFixed(2)} MB` : '-'}
                  </td>
                  <td>
                    {solution.tests_passed !== undefined ? (
                      <span className={`font-mono ${
                        solution.tests_passed === solution.total_tests 
                          ? 'text-green-400' 
                          : solution.tests_passed > 0 
                            ? 'text-yellow-400' 
                            : 'text-red-400'
                      }`}>
                        {solution.tests_passed}/{solution.total_tests || '-'}
                      </span>
                    ) : (
                      '-'
                    )}
                  </td>
                </>
              )}
              <td>
                <StatusBadge status={solution.status || 'pending'} size="sm" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default ScoreBoard;