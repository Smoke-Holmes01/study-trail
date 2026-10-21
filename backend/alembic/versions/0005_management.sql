ALTER TABLE users ADD COLUMN role varchar(8) NOT NULL DEFAULT 'user', ADD COLUMN enabled boolean NOT NULL DEFAULT true, ADD COLUMN last_login_at timestamptz, ADD CONSTRAINT users_role CHECK (role IN ('user','admin'));

ALTER TABLE sessions ADD COLUMN realm varchar(8) NOT NULL DEFAULT 'user', ADD CONSTRAINT sessions_realm CHECK (realm IN ('user','admin'));

UPDATE sessions SET revoked_at=now();

ALTER TABLE files ADD COLUMN index_fingerprint varchar(64);

ALTER TABLE document_chunks ADD COLUMN index_fingerprint varchar(64);


CREATE TABLE admin_operation_logs (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	actor_id UUID NOT NULL, 
	action VARCHAR(80) NOT NULL, 
	target_kind VARCHAR(24) NOT NULL, 
	target_id UUID, 
	result VARCHAR(16) NOT NULL, 
	safe_error_code VARCHAR(80), 
	request_id UUID NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
)

;

CREATE INDEX ix_admin_operation_logs_occurred_at ON admin_operation_logs (occurred_at);

CREATE INDEX ix_admin_operation_logs_actor_id ON admin_operation_logs (actor_id);


CREATE TABLE invocation_attempts (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	operation_key TEXT NOT NULL, 
	attempt_no INTEGER NOT NULL, 
	task_id UUID, 
	actor_id UUID NOT NULL, 
	purpose VARCHAR(32) NOT NULL, 
	category VARCHAR(16) NOT NULL, 
	model_kind VARCHAR(16), 
	config_id UUID, 
	revision_id UUID, 
	tool_name VARCHAR(200), 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(16) NOT NULL, 
	safe_error_code VARCHAR(80), 
	PRIMARY KEY (id), 
	UNIQUE (operation_key, attempt_no), 
	CHECK (status IN ('running','succeeded','failed','stopped','unknown'))
)

;

CREATE INDEX ix_invocation_attempts_started_at ON invocation_attempts (started_at);

CREATE INDEX ix_invocation_attempts_task_id ON invocation_attempts (task_id);

CREATE INDEX ix_invocation_attempts_purpose ON invocation_attempts (purpose);


CREATE TABLE metric_coverage (
	metric_key TEXT NOT NULL, 
	coverage_start TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	history_complete BOOLEAN NOT NULL, 
	note_code TEXT NOT NULL, 
	PRIMARY KEY (metric_key)
)

;


CREATE TABLE platform_configs (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	stable_id VARCHAR(80) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	description TEXT DEFAULT '' NOT NULL, 
	enabled BOOLEAN DEFAULT false NOT NULL, 
	purpose VARCHAR(48), 
	current_revision_id UUID NOT NULL, 
	lock_version INTEGER DEFAULT 1 NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (kind, stable_id), 
	CHECK (kind IN ('model','prompt','skill','mcp','limits')), 
	CHECK (lock_version > 0)
)

;

CREATE UNIQUE INDEX uq_active_prompt_purpose ON platform_configs (purpose) WHERE kind = 'prompt' AND enabled;


CREATE TABLE resource_events (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	event_key TEXT NOT NULL, 
	owner_id UUID NOT NULL, 
	resource_kind VARCHAR(24) NOT NULL, 
	resource_id UUID NOT NULL, 
	action TEXT DEFAULT 'created' NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	origin TEXT DEFAULT 'live' NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (event_key)
)

;

CREATE INDEX ix_resource_events_owner_id ON resource_events (owner_id);

CREATE INDEX ix_resource_events_occurred_at ON resource_events (occurred_at);


CREATE TABLE task_run_facts (
	task_id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	source_agent_id UUID, 
	source_file_id UUID, 
	file_extension VARCHAR(8), 
	kind VARCHAR(32) NOT NULL, 
	action VARCHAR(32), 
	accepted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(16) NOT NULL, 
	model_config_id UUID, 
	safe_error_code VARCHAR(80), 
	PRIMARY KEY (task_id), 
	CHECK (status IN ('queued','running','succeeded','failed','stopped'))
)

;

CREATE INDEX ix_task_run_facts_status ON task_run_facts (status);

CREATE INDEX ix_task_run_facts_owner_id ON task_run_facts (owner_id);

CREATE INDEX ix_task_run_facts_accepted_at ON task_run_facts (accepted_at);


CREATE TABLE config_revisions (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	config_id UUID NOT NULL, 
	revision_no INTEGER NOT NULL, 
	payload JSONB NOT NULL, 
	credential_version_ids JSONB DEFAULT '{}'::jsonb NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	created_by UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(config_id) REFERENCES platform_configs (id), 
	UNIQUE (config_id, revision_no), 
	UNIQUE (config_id, id), 
	CHECK (revision_no > 0)
)

;


CREATE TABLE credential_versions (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	config_id UUID NOT NULL, 
	slot VARCHAR(80) NOT NULL, 
	version_no INTEGER NOT NULL, 
	ciphertext BYTEA NOT NULL, 
	key_id TEXT NOT NULL, 
	created_by UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(config_id) REFERENCES platform_configs (id), 
	UNIQUE (config_id, slot, version_no)
)

;


CREATE TABLE migration_imports (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	source_key TEXT NOT NULL, 
	source_sha256 VARCHAR(64) NOT NULL, 
	config_id UUID NOT NULL, 
	revision_id UUID NOT NULL, 
	imported_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(config_id, revision_id) REFERENCES config_revisions (config_id, id), 
	UNIQUE (source_key)
)

;


CREATE TABLE platform_bindings (
	slot VARCHAR(48) NOT NULL, 
	config_id UUID NOT NULL, 
	revision_id UUID NOT NULL, 
	index_fingerprint VARCHAR(64), 
	lock_version INTEGER DEFAULT 1 NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (slot), 
	FOREIGN KEY(config_id, revision_id) REFERENCES config_revisions (config_id, id)
)

;

ALTER TABLE platform_configs ADD CONSTRAINT fk_platform_current_revision FOREIGN KEY(id, current_revision_id) REFERENCES config_revisions (config_id, id) DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION reject_platform_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'platform history is immutable'; END; $$;

CREATE TRIGGER immutable_config_revisions BEFORE UPDATE OR DELETE ON config_revisions FOR EACH ROW EXECUTE FUNCTION reject_platform_history_change();

CREATE TRIGGER immutable_credential_versions BEFORE UPDATE OR DELETE ON credential_versions FOR EACH ROW EXECUTE FUNCTION reject_platform_history_change();

CREATE TRIGGER immutable_admin_operation_logs BEFORE UPDATE OR DELETE ON admin_operation_logs FOR EACH ROW EXECUTE FUNCTION reject_platform_history_change();

CREATE TRIGGER immutable_resource_events BEFORE UPDATE OR DELETE ON resource_events FOR EACH ROW EXECUTE FUNCTION reject_platform_history_change();