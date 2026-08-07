// Purpose: Visualizer component overlaying bounding boxes onto document page preview image.
// Future TODOs: Support real image dimension scaling, zoom/pan controls, and active box click corrections.

import React, { useState } from 'react';
import { Block } from '../api/client';
import { Eye, ShieldCheck, Sparkles } from 'lucide-react';

interface PagePreviewProps {
  imagePreviewUrl: string;
  blocks: Block[];
  pageNumber: number;
}

export const PagePreview: React.FC<PagePreviewProps> = ({ imagePreviewUrl, blocks, pageNumber }) => {
  const [hoveredBlock, setHoveredBlock] = useState<Block | null>(null);

  // Mock dimensions representing document page aspect ratios
  const mockPageWidth = 600;
  const mockPageHeight = 800;

  return (
    <div className="flex flex-col h-full space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-slate-200 font-semibold flex items-center gap-2">
          <Eye size={18} className="text-indigo-400" />
          Layout Visualizer — Page {pageNumber}
        </h3>
        <div className="flex gap-4 text-xs">
          <span className="flex items-center gap-1 text-emerald-400">
            <span className="w-2.5 h-2.5 rounded bg-emerald-500/20 border border-emerald-500/50" />
            PaddleOCR
          </span>
          <span className="flex items-center gap-1 text-cyan-400">
            <span className="w-2.5 h-2.5 rounded bg-cyan-500/20 border border-cyan-500/50" />
            Surya Fallback
          </span>
        </div>
      </div>

      <div className="flex-1 glass-panel rounded-2xl p-4 border border-slate-800 flex items-center justify-center relative overflow-hidden min-h-[450px]">
        {/* Render base page mock container */}
        <div 
          className="relative bg-slate-950 border border-slate-800 rounded-lg shadow-2xl"
          style={{ width: `${mockPageWidth}px`, height: `${mockPageHeight}px`, maxWidth: '100%', aspectRatio: '3/4' }}
        >
          {/* Mock page base template background */}
          <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-700 opacity-20 p-8 select-none">
            <p className="text-sm font-semibold tracking-widest uppercase">Document Raster Preview</p>
            <p className="text-xs text-center mt-1">Image preview URL: {imagePreviewUrl}</p>
          </div>

          {/* Render layout overlay bounding boxes */}
          {blocks.map((block) => {
            const [x0, y0, x1, y1] = block.bbox;
            // Map coordinates assuming bounding box scales to percent
            const left = `${(x0 / mockPageWidth) * 100}%`;
            const top = `${(y0 / mockPageHeight) * 100}%`;
            const width = `${((x1 - x0) / mockPageWidth) * 100}%`;
            const height = `${((y1 - y0) / mockPageHeight) * 100}%`;

            const isSurya = block.engine_used === 'surya';
            const borderClass = isSurya 
              ? 'border-cyan-500/50 bg-cyan-500/5 hover:bg-cyan-500/20' 
              : 'border-emerald-500/50 bg-emerald-500/5 hover:bg-emerald-500/20';

            return (
              <div
                key={block.block_id}
                className={`absolute border rounded cursor-pointer transition-all duration-200 ${borderClass}`}
                style={{ left, top, width, height }}
                onMouseEnter={() => setHoveredBlock(block)}
                onMouseLeave={() => setHoveredBlock(null)}
              />
            );
          })}
        </div>

        {/* Floating details box on hover */}
        {hoveredBlock && (
          <div className="absolute bottom-4 left-4 right-4 bg-slate-900/90 border border-slate-800 backdrop-blur-md rounded-xl p-4 shadow-xl z-20 animate-fade-in flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-slate-400 text-xs uppercase font-mono">{hoveredBlock.block_id}</span>
                <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px] font-semibold uppercase">
                  {hoveredBlock.type}
                </span>
              </div>
              <p className="text-slate-200 text-sm font-medium mt-1 truncate max-w-[320px]">
                "{hoveredBlock.text}"
              </p>
            </div>
            <div className="text-right border-l border-slate-800 pl-4">
              <div className="flex items-center gap-1.5 justify-end">
                {hoveredBlock.engine_used === 'surya' ? (
                  <span className="flex items-center gap-1 text-[11px] font-semibold text-cyan-400 bg-cyan-950/20 border border-cyan-900/50 px-2 py-0.5 rounded">
                    <Sparkles size={10} />
                    Surya
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-[11px] font-semibold text-emerald-400 bg-emerald-950/20 border border-emerald-900/50 px-2 py-0.5 rounded">
                    <ShieldCheck size={10} />
                    PaddleOCR
                  </span>
                )}
              </div>
              <p className="text-slate-400 text-xs mt-1">
                Confidence: <span className="font-semibold text-slate-200">{(hoveredBlock.confidence * 100).toFixed(0)}%</span>
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
