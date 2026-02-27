import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import StatusBadge from './StatusBadge';

function RoundTimeline({ rounds, onRoundClick }) {
  const scrollContainerRef = React.useRef(null);

  const scroll = (direction) => {
    if (scrollContainerRef.current) {
      const scrollAmount = 200;
      scrollContainerRef.current.scrollBy({
        left: direction === 'left' ? -scrollAmount : scrollAmount,
        behavior: 'smooth',
      });
    }
  };

  const getScoreColor = (score, maxScore = 100) => {
    const ratio = score / maxScore;
    if (ratio >= 0.8) return 'bg-green-500';
    if (ratio >= 0.5) return 'bg-yellow-500';
    if (ratio > 0) return 'bg-orange-500';
    return 'bg-red-500';
  };

  return (
    <div className="relative">
      {/* Scroll buttons */}
      <button
        onClick={() => scroll('left')}
        className="absolute left-0 top-1/2 -translate-y-1/2 z-10 bg-gray-800 hover:bg-gray-700 text-gray-300 p-2 rounded-full shadow-lg border border-gray-700"
      >
        <ChevronLeft className="h-5 w-5" />
      </button>
      <button
        onClick={() => scroll('right')}
        className="absolute right-0 top-1/2 -translate-y-1/2 z-10 bg-gray-800 hover:bg-gray-700 text-gray-300 p-2 rounded-full shadow-lg border border-gray-700"
      >
        <ChevronRight className="h-5 w-5" />
      </button>

      {/* Timeline container */}
      <div
        ref={scrollContainerRef}
        className="flex space-x-4 overflow-x-auto py-4 px-8 scrollbar-hide"
        style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
      >
        {rounds.map((round) => (
          <button
            key={round.id}
            onClick={() => onRoundClick(round)}
            className="flex-shrink-0 w-32 bg-gray-800 hover:bg-gray-750 border border-gray-700 rounded-lg p-3 transition-all hover:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-bold text-gray-300">
                R{round.round_number}
              </span>
              <StatusBadge status={round.status} size="sm" />
            </div>
            
            {/* Model scores */}
            <div className="space-y-1">
              {round.solutions?.map((solution) => (
                <div key={solution.model_slug} className="flex items-center space-x-2">
                  <div 
                    className={`h-2 flex-1 rounded-full ${getScoreColor(solution.score || 0)}`}
                    style={{ width: `${Math.max((solution.score || 0), 5)}%` }}
                  />
                  <span className="text-xs font-mono text-gray-400 w-10 text-right">
                    {solution.score?.toFixed(0) || 0}
                  </span>
                </div>
              ))}
            </div>

            {/* Summary stats */}
            <div className="mt-2 pt-2 border-t border-gray-700 text-xs text-gray-500">
              {round.status === 'completed' && (
                <span>
                  {round.solutions?.filter(s => s.tests_passed > 0).length || 0} models passed
                </span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

export default RoundTimeline;