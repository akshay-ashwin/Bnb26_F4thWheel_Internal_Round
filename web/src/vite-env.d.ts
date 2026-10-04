/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "true" serves every API call from the in-browser mock (demo mode). */
  readonly VITE_USE_MOCKS?: string;
  /** Comma-separated drop ids to show on the home page when using the real API. */
  readonly VITE_DROP_IDS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
