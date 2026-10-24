/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the backend API (see frontend/.env.example). */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
