type P = { className?: string; size?: number };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 16 16",
  fill: "none" as const,
  "aria-hidden": true,
});

const stroke = {
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export const IconMenu = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M2 4h12M2 8h12M2 12h12" {...stroke} />
  </svg>
);

export const IconPlus = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M8 3v10M3 8h10" {...stroke} />
  </svg>
);

export const IconFile = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M4 1.5h5L12.5 5v9a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1z" {...stroke} />
    <path d="M9 1.5V5h3.5" {...stroke} />
  </svg>
);

export const IconArchive = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M1.5 3.5h13v2.5h-13z" {...stroke} />
    <path d="M2.8 6v7a1 1 0 0 0 1 1h8.4a1 1 0 0 0 1-1V6" {...stroke} />
    <path d="M6.5 9h3" {...stroke} />
  </svg>
);

export const IconTrash = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M2.5 4h11M6 4V2.5h4V4M4 4v9.5a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4" {...stroke} />
    <path d="M6.5 7v4.5M9.5 7v4.5" {...stroke} />
  </svg>
);

// A cog, drawn on a 24-unit grid because the toothed outline needs the room.
// What was here before - a small circle with eight radiating spokes - is the
// conventional drawing of a *sun*, and it sat next to the account row where a
// theme switch would live, so it was read as one.
export const IconSettings = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)} viewBox="0 0 24 24">
    <circle cx="12" cy="12" r="3" {...stroke} strokeWidth={2} />
    <path
      d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"
      {...stroke}
      strokeWidth={2}
    />
  </svg>
);

export const IconSend = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M2 8h10M8 4l4 4-4 4" {...stroke} />
  </svg>
);

export const IconCheck = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M3 8.5L6.2 11.5L13 4.5" {...stroke} strokeWidth={1.8} />
  </svg>
);

export const IconX = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M4 4l8 8M12 4l-8 8" {...stroke} />
  </svg>
);

export const IconChevronLeft = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M10 3L5 8l5 5" {...stroke} />
  </svg>
);

export const IconChevronRight = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M6 3l5 5-5 5" {...stroke} />
  </svg>
);

export const IconSearchOff = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <circle cx="7" cy="7" r="4.5" {...stroke} />
    <path d="M10.4 10.4L14 14M4.2 4.2l5.6 5.6" {...stroke} />
  </svg>
);

export const IconSearch = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <circle cx="7" cy="7" r="4.5" {...stroke} />
    <path d="M10.4 10.4L14 14" {...stroke} />
  </svg>
);

export const IconAlert = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M8 1.8L14.6 13.5H1.4L8 1.8z" {...stroke} />
    <path d="M8 6.3v3.1M8 11.4v.01" {...stroke} />
  </svg>
);

export const IconSun = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <circle cx="8" cy="8" r="3" {...stroke} />
    <path d="M8 1v1.6M8 13.4V15M15 8h-1.6M2.6 8H1M12.9 3.1l-1.1 1.1M4.2 11.8l-1.1 1.1M12.9 12.9l-1.1-1.1M4.2 4.2L3.1 3.1" {...stroke} />
  </svg>
);

export const IconMoon = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M13.8 9.6A5.9 5.9 0 1 1 6.6 2.3a4.8 4.8 0 0 0 7.2 7.3z" {...stroke} />
  </svg>
);

export const IconMonitor = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <rect x="1.3" y="2.3" width="13.4" height="9" rx="1" {...stroke} />
    <path d="M5.5 13.7h5M8 11.3v2.4" {...stroke} />
  </svg>
);

export const IconCopy = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <rect x="5.5" y="5.5" width="8" height="8" rx="1" {...stroke} />
    <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" {...stroke} />
  </svg>
);

export const IconThumbUp = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M5 14V7l3-5a1.6 1.6 0 0 1 1.6 1.9L9 7h3.6a1.4 1.4 0 0 1 1.3 1.8l-1.2 4A1.6 1.6 0 0 1 11.2 14H5z" {...stroke} />
    <path d="M5 7H2.6v7H5" {...stroke} />
  </svg>
);

export const IconThumbDown = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M11 2v7l-3 5a1.6 1.6 0 0 1-1.6-1.9L7 9H3.4a1.4 1.4 0 0 1-1.3-1.8l1.2-4A1.6 1.6 0 0 1 4.8 2H11z" {...stroke} />
    <path d="M11 9h2.4V2H11" {...stroke} />
  </svg>
);

export const IconUser = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <circle cx="8" cy="5.5" r="2.7" {...stroke} />
    <path d="M2.8 14c0-2.6 2.3-4.2 5.2-4.2s5.2 1.6 5.2 4.2" {...stroke} />
  </svg>
);

export const IconMore = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <circle cx="3.2" cy="8" r="1.1" fill="currentColor" />
    <circle cx="8" cy="8" r="1.1" fill="currentColor" />
    <circle cx="12.8" cy="8" r="1.1" fill="currentColor" />
  </svg>
);

export const IconRestore = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M2.6 8a5.4 5.4 0 1 0 1.6-3.8" {...stroke} />
    <path d="M2.2 2.6v3.2h3.2" {...stroke} />
  </svg>
);

export const IconEdit = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M11.2 2.3l2.5 2.5-8 8-3.2.7.7-3.2 8-8z" {...stroke} />
  </svg>
);

export const IconSparkle = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <path d="M8 1.8l1.5 3.9L13.4 7.2 9.5 8.7 8 12.6 6.5 8.7 2.6 7.2 6.5 5.7 8 1.8z" {...stroke} />
  </svg>
);

export const IconPanelRight = ({ className, size = 16 }: P) => (
  <svg className={className} {...base(size)}>
    <rect x="1.5" y="2.5" width="13" height="11" rx="1.2" {...stroke} />
    <path d="M10 2.5v11" {...stroke} />
  </svg>
);
