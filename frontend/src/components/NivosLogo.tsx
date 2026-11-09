import React from 'react';

interface NivosLogoProps {
  size?: number;
  className?: string;
  rounded?: boolean;
}

export function NivosLogo({ size = 28, className = '', rounded = true }: NivosLogoProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 512 512"
      width={size}
      height={size}
      className={className}
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 }}
      aria-label="Nivos logo"
    >
      <defs>
        <linearGradient id="nivosBgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#0f172a" />
          <stop offset="50%" stopColor="#090e1a" />
          <stop offset="100%" stopColor="#020617" />
        </linearGradient>

        <linearGradient id="nivosCyanPillars" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="50%" stopColor="#06b6d4" />
          <stop offset="100%" stopColor="#0284c7" />
        </linearGradient>

        <linearGradient id="nivosCyanDiagonal" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#67e8f9" />
          <stop offset="45%" stopColor="#22d3ee" />
          <stop offset="100%" stopColor="#0891b2" />
        </linearGradient>

        <filter id="nivosGlow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="6" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      {/* Dark rounded square base */}
      <rect
        x="24"
        y="24"
        width="464"
        height="464"
        rx={rounded ? 104 : 0}
        ry={rounded ? 104 : 0}
        fill="url(#nivosBgGrad)"
        stroke="#1e293b"
        strokeWidth="6"
      />

      {/* Subtle neon rim accent */}
      <rect
        x="28"
        y="28"
        width="456"
        height="456"
        rx={rounded ? 100 : 0}
        ry={rounded ? 100 : 0}
        fill="none"
        stroke="#22d3ee"
        strokeWidth="2"
        strokeOpacity="0.3"
      />

      {/* Cyan N monogram */}
      <g filter="url(#nivosGlow)">
        <line
          x1="158"
          y1="148"
          x2="354"
          y2="364"
          stroke="url(#nivosCyanDiagonal)"
          strokeWidth="58"
          strokeLinecap="round"
        />
        <line
          x1="158"
          y1="148"
          x2="158"
          y2="364"
          stroke="url(#nivosCyanPillars)"
          strokeWidth="58"
          strokeLinecap="round"
        />
        <line
          x1="354"
          y1="148"
          x2="354"
          y2="364"
          stroke="url(#nivosCyanPillars)"
          strokeWidth="58"
          strokeLinecap="round"
        />

        <circle cx="256" cy="256" r="10" fill="#e0f2fe" opacity="0.95" />
        <polygon points="256,236 268,256 256,276 244,256" fill="#a5f3fc" opacity="0.8" />
      </g>
    </svg>
  );
}
