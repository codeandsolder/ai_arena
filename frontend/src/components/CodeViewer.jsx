import React, { useEffect, useRef } from 'react';

function CodeViewer({ code, language = 'cpp', showLineNumbers = true }) {
  const codeRef = useRef(null);

  useEffect(() => {
    if (codeRef.current && window.hljs) {
      window.hljs.highlightElement(codeRef.current);
    }
  }, [code, language]);

  const lines = code.split('\n');

  return (
    <div className="relative rounded-lg overflow-hidden bg-gray-950 border border-gray-800">
      <div className="flex">
        {showLineNumbers && (
          <div className="select-none bg-gray-900 text-gray-500 text-right pr-4 pl-2 py-4 font-mono text-sm border-r border-gray-800">
            {lines.map((_, index) => (
              <div key={index} className="leading-6">
                {index + 1}
              </div>
            ))}
          </div>
        )}
        <div className="flex-1 overflow-x-auto">
          <pre className="m-0 p-4">
            <code
              ref={codeRef}
              className={`language-${language} font-mono text-sm leading-6`}
            >
              {code}
            </code>
          </pre>
        </div>
      </div>
    </div>
  );
}

export default CodeViewer;