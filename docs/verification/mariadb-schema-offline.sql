CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE audit_events (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    actor VARCHAR(64) NOT NULL, 
    action VARCHAR(64) NOT NULL, 
    subject_id VARCHAR(64) NOT NULL, 
    details JSON NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE drafts (
    id VARCHAR(36) NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    operation VARCHAR(32) NOT NULL, 
    host_ids JSON NOT NULL, 
    parameters JSON NOT NULL, 
    revision INTEGER NOT NULL, 
    owner VARCHAR(64) NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    updated_at VARCHAR(32) NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE login_attempts (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    client VARCHAR(128) NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_login_attempts_client ON login_attempts (client);

CREATE INDEX ix_login_attempts_created_at ON login_attempts (created_at);

CREATE TABLE nodes (
    id VARCHAR(64) NOT NULL, 
    alias VARCHAR(64) NOT NULL, 
    display_name VARCHAR(128) NOT NULL, 
    hostname VARCHAR(255) NOT NULL, 
    port INTEGER NOT NULL, 
    ssh_username VARCHAR(64) NOT NULL, 
    enabled BOOL NOT NULL, 
    inventory_revision INTEGER NOT NULL, 
    host_key_sha256 VARCHAR(128), 
    hardware JSON NOT NULL, 
    snapshot JSON, 
    observed_at VARCHAR(32), 
    facts JSON NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (alias)
);

CREATE TABLE projects (
    id VARCHAR(36) NOT NULL, 
    name VARCHAR(128) NOT NULL, 
    path VARCHAR(1024) NOT NULL, 
    entrypoint VARCHAR(512) NOT NULL, 
    modes JSON NOT NULL, 
    container VARCHAR(1024) NOT NULL, 
    revision VARCHAR(128) NOT NULL, 
    owner VARCHAR(64) NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE schedules (
    id VARCHAR(36) NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    operation VARCHAR(32) NOT NULL, 
    host_ids JSON NOT NULL, 
    interval_seconds INTEGER NOT NULL, 
    starts_at VARCHAR(32) NOT NULL, 
    expires_at VARCHAR(32) NOT NULL, 
    scope JSON NOT NULL, 
    revision INTEGER NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    enabled BOOL NOT NULL, 
    plan_document JSON, 
    content_hash VARCHAR(64), 
    approved_hash VARCHAR(64), 
    approved_by VARCHAR(64), 
    approved_at VARCHAR(32), 
    owner VARCHAR(64) NOT NULL, 
    next_run_at VARCHAR(32), 
    active_execution_id VARCHAR(36), 
    issues JSON NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_schedules_enabled ON schedules (enabled);

CREATE INDEX ix_schedules_expires_at ON schedules (expires_at);

CREATE TABLE web_sessions (
    token_hash VARCHAR(64) NOT NULL, 
    username VARCHAR(64) NOT NULL, 
    csrf VARCHAR(96) NOT NULL, 
    auth_revision VARCHAR(64) NOT NULL, 
    expires_at VARCHAR(32) NOT NULL, 
    PRIMARY KEY (token_hash)
);

CREATE INDEX ix_web_sessions_expires_at ON web_sessions (expires_at);

CREATE TABLE plans (
    id VARCHAR(36) NOT NULL, 
    draft_id VARCHAR(36), 
    revision INTEGER NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    document JSON NOT NULL, 
    issues JSON NOT NULL, 
    owner VARCHAR(64) NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    approved_at VARCHAR(32), 
    approval_expires_at VARCHAR(32), 
    approved_by VARCHAR(64), 
    reason TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(draft_id) REFERENCES drafts (id)
);

CREATE INDEX ix_plans_status ON plans (status);

CREATE TABLE executions (
    id VARCHAR(36) NOT NULL, 
    plan_id VARCHAR(36), 
    schedule_id VARCHAR(36), 
    trigger_key VARCHAR(160) NOT NULL, 
    authorization_kind VARCHAR(16) NOT NULL, 
    approved_hash VARCHAR(64) NOT NULL, 
    plan_document JSON NOT NULL, 
    owner VARCHAR(64) NOT NULL, 
    mode VARCHAR(32) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    started_at VARCHAR(32), 
    finished_at VARCHAR(32), 
    worker_id VARCHAR(64), 
    lease_until VARCHAR(32), 
    stop_requested BOOL NOT NULL, 
    log_complete BOOL NOT NULL, 
    error TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(plan_id) REFERENCES plans (id), 
    FOREIGN KEY(schedule_id) REFERENCES schedules (id), 
    UNIQUE (trigger_key)
);

CREATE INDEX ix_executions_status ON executions (status);

CREATE TABLE execution_logs (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    execution_id VARCHAR(36) NOT NULL, 
    step_id VARCHAR(64), 
    host_id VARCHAR(64), 
    stream VARCHAR(16) NOT NULL, 
    text TEXT NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(execution_id) REFERENCES executions (id)
);

CREATE INDEX ix_execution_logs_cursor ON execution_logs (execution_id, id);

CREATE TABLE execution_steps (
    id VARCHAR(36) NOT NULL, 
    execution_id VARCHAR(36) NOT NULL, 
    step_id VARCHAR(64) NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    host_id VARCHAR(64) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    started_at VARCHAR(32), 
    finished_at VARCHAR(32), 
    exit_code INTEGER, 
    `signal` VARCHAR(64), 
    error TEXT, 
    outputs JSON NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(execution_id) REFERENCES executions (id), 
    UNIQUE (execution_id, step_id)
);

CREATE INDEX ix_execution_steps_execution_id ON execution_steps (execution_id);

CREATE TABLE jobs (
    id VARCHAR(36) NOT NULL, 
    slurm_job_id VARCHAR(64) NOT NULL, 
    project_id VARCHAR(36), 
    profile VARCHAR(32) NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    execution_id VARCHAR(36) NOT NULL, 
    owner VARCHAR(64) NOT NULL, 
    created_at VARCHAR(32) NOT NULL, 
    snapshot JSON NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(execution_id) REFERENCES executions (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id), 
    UNIQUE (execution_id)
);

CREATE TABLE artifacts (
    id VARCHAR(36) NOT NULL, 
    job_id VARCHAR(36) NOT NULL, 
    owner VARCHAR(64) NOT NULL, 
    filename VARCHAR(255) NOT NULL, 
    relative_path VARCHAR(1024) NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    size INTEGER NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(job_id) REFERENCES jobs (id)
);

INSERT INTO alembic_version (version_num) VALUES ('0001');

-- Running upgrade 0001 -> 0002

CREATE TABLE job_log_cursors (
    id VARCHAR(36) NOT NULL, 
    job_id VARCHAR(36) NOT NULL, 
    stream VARCHAR(16) NOT NULL, 
    offset BIGINT NOT NULL, 
    inode VARCHAR(64), 
    device VARCHAR(64), 
    observed_size BIGINT NOT NULL, 
    has_more BOOL NOT NULL, 
    gap BOOL NOT NULL, 
    observed_at VARCHAR(32) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(job_id) REFERENCES jobs (id), 
    UNIQUE (job_id, stream)
);

CREATE INDEX ix_job_log_cursors_job_id ON job_log_cursors (job_id);

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';

