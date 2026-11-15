export type Mode = 'demo' | 'local-slurm' | 'remote-slurm';
export interface Session { username: string; csrf_token: string; permissions: string[]; mode: Mode }
export interface Health { status: string; mode: Mode; version: string; configured: boolean }
export interface HardwareGpu { index: number; name: string; memory_mb: number; uuid: string; in_scope: boolean }
export interface SnapshotGpu { uuid?: string; index: number; name: string; memory_used_mb: number | null; memory_total_mb: number | null; utilization_percent: number | null; in_scope?: boolean }
export interface Node {
  id: string; alias: string; display_name: string; hostname: string; ssh_username: string;
  enabled: boolean; inventory_revision: string | number; host_key_sha256: string | null;
  hardware: { cpu_model?: string; logical_cpus?: number; memory_gb?: number; gpus?: HardwareGpu[]; network_mbps?: number };
  snapshot: { cpu_percent?: number; memory_used_mb?: number; memory_total_mb?: number; gpus?: SnapshotGpu[]; storage?: StorageReading[]; network?: NetworkReading[]; uptime_seconds?: number; gpu_error?: string | null; status: string; source: string; collected_at: string } | null;
  observed_at: string | null;
  ssh_connection?: SSHHost | null;
}
export interface Project { id: string; name: string; path: string; entrypoint: string; modes: string[]; container: string; revision: string }
export type Operation = 'preflight' | 'submit_job' | 'cancel_job' | 'collect_results' | 'deploy' | 'write_config' | 'cleanup';
export interface Draft { id: string; title: string; operation: Operation; host_ids: string[]; parameters: Record<string, unknown>; created_at?: string; revision?: number }
export interface PlanStep {
  id: string; host_id?: string; title: string; kind: string; argv?: string[]; script?: string;
  cwd?: string; environment?: Record<string, unknown>; request?: unknown; file_change?: unknown;
  reason?: string; evidence?: unknown; requires_sudo?: boolean; execution_user?: string;
  timeout_seconds?: number; preconditions?: unknown; on_failure?: unknown;
}
export interface PlanDocument {
  schema_version?: string; mode?: Mode; authorization_kind?: string; inventory?: unknown;
  steps: PlanStep[]; impact?: unknown; failure_policy?: unknown; source?: unknown;
  [key: string]: unknown;
}
export interface Plan { id: string; draft_id: string; revision: number; title: string; status: string; content_hash: string; document: PlanDocument; created_at: string; approved_at?: string | null; approval_expires_at?: string | null; issues: unknown[] }
export interface ExecutionStep { id: string; step_id: string; title: string; host_id: string; status: string; started_at?: string; finished_at?: string; exit_code?: number | null; error?: string; outputs?: unknown }
export interface Execution { id: string; plan_id?: string | null; schedule_id?: string | null; status: string; mode: Mode; created_at: string; started_at?: string | null; finished_at?: string | null; steps: ExecutionStep[]; log_complete: boolean; error?: string | null; reconciliation_supported?: boolean }
export interface LogLine { id: string; sequence: number; step_id: string; host_id: string; stream: 'stdout' | 'stderr' | 'audit'; text: string; created_at: string }
export interface LogResponse { items: LogLine[]; next_cursor: number }
export interface Schedule { id: string; title: string; operation: 'host_snapshot' | 'job_status' | 'job_logs'; host_ids: string[]; interval_seconds: number; starts_at: string; expires_at: string; scope: { job_ids: string[] }; status: string; revision: number; enabled?: boolean; plan_document?: PlanDocument | null; content_hash?: string | null; issues?: unknown[]; last_run_at?: string | null; next_run_at?: string | null }
export interface Job { id: string; slurm_job_id: string | number; project_id: string; profile: string; status: string; execution_id?: string; owner: string; created_at: string; [key: string]: unknown }
export interface Artifact { id: string; name?: string; filename?: string; size?: number; size_bytes?: number; content_type?: string }
export interface OverviewData { nodes: Node[]; projects: Project[]; drafts: Draft[]; plans: Plan[]; executions: Execution[]; schedules: Schedule[]; jobs: Job[] }

export interface MonitorResult {
  ping?: { status: 'ok' | 'timeout' | 'unavailable'; ms: number | null; checked_at: string };
  status: 'online' | 'error'; error: string | null; checked_at: string;
  snapshot: Node['snapshot']; host_key_sha256?: string; ssh_username?: string;
}
export interface MonitorState {
  active: boolean; generation: string; host_ids: string[]; results: Record<string, MonitorResult>;
  expires_at?: string; checked_at?: string | null; interval_seconds: number;
}

export interface StorageReading { device: string; mountpoint: string; filesystem: string; total_bytes: number; used_bytes: number; available_bytes: number }
export interface NetworkReading { name: string; state: string; speed_mbps: number | null; rx_bytes_per_second: number | null; tx_bytes_per_second: number | null }
export interface HardwareProfile {
  hostname: string; os: string; kernel: string; architecture: string; cpu_model: string;
  logical_cpus: number; physical_cores: number | null; memory_total_mb: number; gpus: SnapshotGpu[];
  disks: { name: string; model: string; size_bytes: number; rotational: boolean; transport: string | null; mountpoints: string[] }[];
  interfaces: { name: string; mac: string | null; mtu: number | null; addresses: string[] }[];
  disk_error: string | null; gpu_error: string | null;
}
export interface SSHHost {
  node_id: string; display_name: string; hostname: string; port: number; username: string;
  enabled: boolean; revision: number; password_configured: boolean; host_key_sha256: string | null;
  pending_host_key_sha256: string | null; status: string; error: string | null;
  profile: HardwareProfile | null; first_seen_at: string | null; profile_updated_at: string | null; requested_at: string | null;
}
