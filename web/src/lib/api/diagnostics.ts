/**
 * What the running app says about itself.
 */

import { getJson } from "@/lib/api/client";

export type DiagnosticsAddress = {
  host: string;
  port: number;
  requestedPort: number;
  /** True when the preferred port was taken and the service took a free one. */
  fallback: boolean;
  url: string;
};

export type DiagnosticsLogFile = {
  path: string;
  exists: boolean;
  bytes: number;
};

export type Diagnostics = {
  version: string;
  pid: number;
  python: string;
  platform: string;
  /** When the service process began, as seconds since the epoch. */
  startedAt: number;
  uptimeSeconds: number;
  address: DiagnosticsAddress | null;
  dataRoot: string;
  projectsRoot: string;
  projectCount: number;
  activeProject: string | null;
  logs: DiagnosticsLogFile | null;
  /** The document this process reads, and whether its secrets have a home. */
  configuration: {
    path: string;
    schemaVersion: number;
    revision: number;
    keychainAvailable: boolean;
  };
  /** True when the desktop shell started this service and restarts it if it dies. */
  supervised: boolean;
  restarts: number;
  /** The last unexpected stop: its exit code, and when, in seconds since the epoch. */
  lastExit: { code: number; at: number } | null;
  /** Stable names of things worth acting on; the interface words them. */
  warnings: string[];
};

export type DiagnosticsLog = {
  path: string | null;
  lines: string[];
};

export function fetchDiagnostics(): Promise<Diagnostics> {
  return getJson<Diagnostics>("/diagnostics");
}

export function fetchDiagnosticsLog(lines = 80): Promise<DiagnosticsLog> {
  return getJson<DiagnosticsLog>(`/diagnostics/log?lines=${lines}`);
}
