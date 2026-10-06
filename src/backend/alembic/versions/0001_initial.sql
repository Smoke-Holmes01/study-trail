
CREATE TABLE users (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	login VARCHAR(32) NOT NULL,
	login_normalized VARCHAR(32) NOT NULL,
	password_hash TEXT NOT NULL,
	display_name VARCHAR(32) NOT NULL,
	theme VARCHAR(8) NOT NULL,
	auth_version INTEGER NOT NULL,
	PRIMARY KEY (id),
	CHECK (theme IN ('light','dark')),
	CHECK (login_normalized ~ '^[a-z0-9_]{3,32}$'),
	UNIQUE (login_normalized)
)

;


CREATE TABLE sessions (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	token_hash BYTEA NOT NULL,
	csrf_token VARCHAR(64) NOT NULL,
	auth_version INTEGER NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	UNIQUE (token_hash)
)

;

CREATE INDEX ix_sessions_owner ON sessions (owner_id);


CREATE TABLE agents (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	name VARCHAR(50) NOT NULL,
	description VARCHAR(500) NOT NULL,
	system_prompt TEXT NOT NULL,
	model_id VARCHAR(200) NOT NULL,
	config_version INTEGER NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_agents_owner ON agents (owner_id);


CREATE TABLE knowledge_bases (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	name VARCHAR(50) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_knowledge_bases_owner ON knowledge_bases (owner_id);


CREATE TABLE sources (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	kind VARCHAR(12) NOT NULL,
	file_id UUID,
	chunk_id UUID,
	title_snapshot TEXT NOT NULL,
	original_file_id UUID,
	url_snapshot TEXT,
	excerpt_snapshot TEXT NOT NULL,
	locator_snapshot JSONB,
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (kind IN ('file','web')),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_sources_file_id ON sources (file_id);

CREATE INDEX ix_sources_chunk_id ON sources (chunk_id);

CREATE INDEX ix_sources_owner ON sources (owner_id);


CREATE TABLE tasks (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	kind VARCHAR(24) NOT NULL,
	action VARCHAR(24),
	agent_id UUID,
	conversation_id UUID,
	user_message_id UUID,
	assistant_message_id UUID,
	file_id UUID,
	target_plan_id UUID,
	base_plan_version INTEGER,
	attempt_no INTEGER NOT NULL,
	previous_task_id UUID,
	status VARCHAR(16) NOT NULL,
	cancel_requested BOOLEAN NOT NULL,
	revision BIGINT NOT NULL,
	input_snapshot JSONB NOT NULL,
	config_snapshot JSONB NOT NULL,
	result_snapshot JSONB NOT NULL,
	phase TEXT NOT NULL,
	preview_text TEXT NOT NULL,
	error_code TEXT,
	error_message TEXT,
	lease_owner TEXT,
	claim_token UUID,
	timeout_seconds INTEGER NOT NULL,
	queued_at TIMESTAMP WITH TIME ZONE NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	deadline_at TIMESTAMP WITH TIME ZONE,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	heartbeat_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	next_attempt_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CHECK (status IN ('queued','running','succeeded','failed','stopped')),
	CHECK (attempt_no >= 1),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	UNIQUE (claim_token)
)

;

CREATE INDEX ix_tasks_file_id ON tasks (file_id);

CREATE INDEX ix_tasks_target_plan_id ON tasks (target_plan_id);

CREATE INDEX ix_tasks_previous_task_id ON tasks (previous_task_id);

CREATE INDEX ix_tasks_owner ON tasks (owner_id);

CREATE INDEX ix_tasks_conversation_id ON tasks (conversation_id);

CREATE INDEX ix_tasks_user_message_id ON tasks (user_message_id);

CREATE UNIQUE INDEX uq_message_attempt ON tasks (user_message_id, attempt_no) WHERE user_message_id IS NOT NULL;

CREATE INDEX ix_tasks_assistant_message_id ON tasks (assistant_message_id);

CREATE INDEX ix_tasks_queue ON tasks (status, next_attempt_at, queued_at);

CREATE UNIQUE INDEX uq_active_chat ON tasks (conversation_id) WHERE kind = 'chat' AND status IN ('queued', 'running');

CREATE INDEX ix_tasks_agent_id ON tasks (agent_id);

CREATE INDEX ix_tasks_lease ON tasks (lease_expires_at);


CREATE TABLE idempotency_records (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	key VARCHAR(128) NOT NULL,
	method TEXT NOT NULL,
	path TEXT NOT NULL,
	request_fingerprint BYTEA NOT NULL,
	response_status INTEGER NOT NULL,
	response_snapshot JSONB NOT NULL,
	resource_refs JSONB NOT NULL,
	resource_kind TEXT,
	resource_id UUID,
	PRIMARY KEY (id),
	UNIQUE (owner_id, key),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_idempotency_records_owner ON idempotency_records (owner_id);


CREATE TABLE agent_knowledge_bases (
	agent_id UUID NOT NULL,
	knowledge_base_id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (agent_id, knowledge_base_id),
	FOREIGN KEY(agent_id, owner_id) REFERENCES agents (id, owner_id) ON DELETE CASCADE,
	FOREIGN KEY(knowledge_base_id, owner_id) REFERENCES knowledge_bases (id, owner_id) ON DELETE CASCADE
)

;

CREATE INDEX ix_agent_knowledge_bases_owner ON agent_knowledge_bases (owner_id);

CREATE INDEX ix_agent_knowledge_bases_knowledge_base_id ON agent_knowledge_bases (knowledge_base_id);

CREATE INDEX ix_agent_knowledge_bases_agent_id ON agent_knowledge_bases (agent_id);


CREATE TABLE conversations (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	agent_id UUID NOT NULL,
	name VARCHAR(50) NOT NULL,
	next_sequence_no BIGINT NOT NULL,
	has_images BOOLEAN NOT NULL,
	last_accessed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(agent_id, owner_id) REFERENCES agents (id, owner_id) ON DELETE CASCADE,
	UNIQUE (id, owner_id, agent_id),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_recent_conversations ON conversations (agent_id, coalesce(last_accessed_at, created_at) DESC, id DESC);

CREATE INDEX ix_conversations_owner ON conversations (owner_id);

CREATE INDEX ix_conversations_agent_id ON conversations (agent_id);


CREATE TABLE files (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	knowledge_base_id UUID NOT NULL,
	original_name VARCHAR(255) NOT NULL,
	extension TEXT NOT NULL,
	media_type TEXT NOT NULL,
	storage_key TEXT NOT NULL,
	sha256 BYTEA NOT NULL,
	byte_size BIGINT NOT NULL,
	status VARCHAR(16) NOT NULL,
	parse_version INTEGER NOT NULL,
	text_snapshot TEXT,
	text_metadata JSONB,
	embedding_model_id TEXT,
	indexed_at TIMESTAMP WITH TIME ZONE,
	error_code TEXT,
	error_message TEXT,
	PRIMARY KEY (id),
	FOREIGN KEY(knowledge_base_id, owner_id) REFERENCES knowledge_bases (id, owner_id) ON DELETE CASCADE,
	UNIQUE (id, owner_id, knowledge_base_id),
	CHECK (status IN ('processing','ready','failed')),
	CHECK (byte_size > 0 AND byte_size <= 52428800),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	UNIQUE (storage_key)
)

;

CREATE INDEX ix_files_owner ON files (owner_id);

CREATE INDEX ix_files_knowledge_base_id ON files (knowledge_base_id);


CREATE TABLE learning_plans (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	agent_id UUID NOT NULL,
	name VARCHAR(50) NOT NULL,
	content JSONB NOT NULL,
	content_version INTEGER NOT NULL,
	source_conversation_id UUID,
	source_message_id UUID,
	last_updated_by_task_id UUID,
	PRIMARY KEY (id),
	FOREIGN KEY(agent_id, owner_id) REFERENCES agents (id, owner_id) ON DELETE CASCADE,
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_learning_plans_source_conversation_id ON learning_plans (source_conversation_id);

CREATE INDEX ix_learning_plans_owner ON learning_plans (owner_id);

CREATE INDEX ix_learning_plans_source_message_id ON learning_plans (source_message_id);

CREATE INDEX ix_learning_plans_last_updated_by_task_id ON learning_plans (last_updated_by_task_id);

CREATE INDEX ix_learning_plans_agent_id ON learning_plans (agent_id);


CREATE TABLE messages (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	agent_id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	sequence_no BIGINT NOT NULL,
	role VARCHAR(16) NOT NULL,
	origin VARCHAR(20) NOT NULL,
	content_text TEXT NOT NULL,
	user_message_id UUID,
	task_id UUID,
	response_status VARCHAR(16),
	answer_exercise_id UUID,
	PRIMARY KEY (id),
	FOREIGN KEY(conversation_id, owner_id, agent_id) REFERENCES conversations (id, owner_id, agent_id) ON DELETE CASCADE,
	UNIQUE (conversation_id, sequence_no),
	UNIQUE (id, owner_id, agent_id, conversation_id),
	CHECK (role IN ('user','assistant')),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	UNIQUE (task_id)
)

;

CREATE INDEX ix_messages_owner ON messages (owner_id);

CREATE INDEX ix_messages_task_id ON messages (task_id);

CREATE INDEX ix_messages_answer_exercise_id ON messages (answer_exercise_id);

CREATE INDEX ix_messages_agent_id ON messages (agent_id);

CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);

CREATE INDEX ix_messages_user_message_id ON messages (user_message_id);


CREATE TABLE attachments (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	agent_id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	state VARCHAR(12) NOT NULL,
	original_name VARCHAR(255) NOT NULL,
	storage_key TEXT NOT NULL,
	media_type VARCHAR(50) NOT NULL,
	byte_size BIGINT NOT NULL,
	width INTEGER NOT NULL,
	height INTEGER NOT NULL,
	sha256 BYTEA NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(conversation_id, owner_id, agent_id) REFERENCES conversations (id, owner_id, agent_id) ON DELETE CASCADE,
	UNIQUE (id, owner_id, agent_id, conversation_id),
	CHECK (state IN ('staged','bound')),
	CHECK (byte_size > 0 AND byte_size <= 10485760),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	UNIQUE (storage_key)
)

;

CREATE INDEX ix_attachments_conversation_id ON attachments (conversation_id);

CREATE INDEX ix_attachments_owner ON attachments (owner_id);

CREATE INDEX ix_attachments_agent_id ON attachments (agent_id);


CREATE TABLE document_chunks (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	knowledge_base_id UUID NOT NULL,
	file_id UUID NOT NULL,
	parse_version INTEGER NOT NULL,
	chunk_index INTEGER NOT NULL,
	content_text TEXT NOT NULL,
	char_start BIGINT NOT NULL,
	char_end BIGINT NOT NULL,
	locator JSONB NOT NULL,
	embedding VECTOR(1024) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(file_id, owner_id, knowledge_base_id) REFERENCES files (id, owner_id, knowledge_base_id) ON DELETE CASCADE,
	UNIQUE (file_id, parse_version, chunk_index),
	CHECK (char_start >= 0 AND char_end > char_start),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_document_chunks_file_id ON document_chunks (file_id);

CREATE INDEX ix_document_chunks_owner ON document_chunks (owner_id);

CREATE INDEX ix_document_chunks_knowledge_base_id ON document_chunks (knowledge_base_id);


CREATE TABLE plan_sources (
	plan_id UUID NOT NULL,
	source_id UUID NOT NULL,
	owner_id UUID NOT NULL,
	position INTEGER NOT NULL,
	content_version INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (plan_id, source_id),
	UNIQUE (plan_id, position),
	FOREIGN KEY(plan_id, owner_id) REFERENCES learning_plans (id, owner_id) ON DELETE CASCADE,
	FOREIGN KEY(source_id, owner_id) REFERENCES sources (id, owner_id) ON DELETE CASCADE
)

;

CREATE INDEX ix_plan_sources_plan_id ON plan_sources (plan_id);

CREATE INDEX ix_plan_sources_owner ON plan_sources (owner_id);

CREATE INDEX ix_plan_sources_source_id ON plan_sources (source_id);


CREATE TABLE conversation_summaries (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	agent_id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	text TEXT NOT NULL,
	through_sequence_no BIGINT NOT NULL,
	covered_user_message_ids UUID[] NOT NULL,
	model_id TEXT NOT NULL,
	config_version INTEGER NOT NULL,
	created_by_task_id UUID,
	PRIMARY KEY (id),
	UNIQUE (conversation_id, through_sequence_no),
	FOREIGN KEY(conversation_id, owner_id, agent_id) REFERENCES conversations (id, owner_id, agent_id) ON DELETE CASCADE,
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_conversation_summaries_owner ON conversation_summaries (owner_id);

CREATE INDEX ix_conversation_summaries_created_by_task_id ON conversation_summaries (created_by_task_id);

CREATE INDEX ix_conversation_summaries_conversation_id ON conversation_summaries (conversation_id);

CREATE INDEX ix_conversation_summaries_agent_id ON conversation_summaries (agent_id);


CREATE TABLE message_attachments (
	message_id UUID NOT NULL,
	attachment_id UUID NOT NULL,
	owner_id UUID NOT NULL,
	agent_id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	position SMALLINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (message_id, attachment_id),
	UNIQUE (message_id, position),
	FOREIGN KEY(message_id, owner_id, agent_id, conversation_id) REFERENCES messages (id, owner_id, agent_id, conversation_id) ON DELETE CASCADE,
	FOREIGN KEY(attachment_id, owner_id, agent_id, conversation_id) REFERENCES attachments (id, owner_id, agent_id, conversation_id) ON DELETE CASCADE,
	CHECK (position >= 0 AND position <= 5),
	UNIQUE (attachment_id)
)

;

CREATE INDEX ix_message_attachments_message_id ON message_attachments (message_id);

CREATE INDEX ix_message_attachments_attachment_id ON message_attachments (attachment_id);

CREATE INDEX ix_message_attachments_owner ON message_attachments (owner_id);

CREATE INDEX ix_message_attachments_agent_id ON message_attachments (agent_id);

CREATE INDEX ix_message_attachments_conversation_id ON message_attachments (conversation_id);


CREATE TABLE message_sources (
	message_id UUID NOT NULL,
	source_id UUID NOT NULL,
	owner_id UUID NOT NULL,
	position INTEGER NOT NULL,
	citation_spans JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (message_id, source_id),
	UNIQUE (message_id, position),
	FOREIGN KEY(message_id, owner_id) REFERENCES messages (id, owner_id) ON DELETE CASCADE,
	FOREIGN KEY(source_id, owner_id) REFERENCES sources (id, owner_id) ON DELETE CASCADE
)

;

CREATE INDEX ix_message_sources_message_id ON message_sources (message_id);

CREATE INDEX ix_message_sources_source_id ON message_sources (source_id);

CREATE INDEX ix_message_sources_owner ON message_sources (owner_id);


CREATE TABLE exercises (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	agent_id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	message_id UUID NOT NULL,
	position INTEGER NOT NULL,
	question_text TEXT NOT NULL,
	provenance VARCHAR(16) NOT NULL,
	source_ids JSONB NOT NULL,
	known_answer_snapshot JSONB,
	PRIMARY KEY (id),
	FOREIGN KEY(message_id, owner_id, agent_id, conversation_id) REFERENCES messages (id, owner_id, agent_id, conversation_id) ON DELETE CASCADE,
	UNIQUE (message_id, position),
	CHECK (provenance IN ('original','adapted','generated')),
	UNIQUE (id, owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX ix_exercises_conversation_id ON exercises (conversation_id);

CREATE INDEX ix_exercises_agent_id ON exercises (agent_id);

CREATE INDEX ix_exercises_owner ON exercises (owner_id);

CREATE INDEX ix_exercises_message_id ON exercises (message_id);

ALTER TABLE sources ADD CONSTRAINT fk_file_id_files_id FOREIGN KEY(file_id) REFERENCES files (id) ON DELETE SET NULL;

ALTER TABLE tasks ADD CONSTRAINT fk_user_message_id_messages_id FOREIGN KEY(user_message_id) REFERENCES messages (id) ON DELETE CASCADE;

ALTER TABLE tasks ADD CONSTRAINT fk_previous_task_id_tasks_id FOREIGN KEY(previous_task_id) REFERENCES tasks (id) ON DELETE SET NULL;

ALTER TABLE conversation_summaries ADD CONSTRAINT fk_created_by_task_id_tasks_id FOREIGN KEY(created_by_task_id) REFERENCES tasks (id) ON DELETE SET NULL;

ALTER TABLE tasks ADD CONSTRAINT fk_assistant_message_id_messages_id FOREIGN KEY(assistant_message_id) REFERENCES messages (id) ON DELETE SET NULL;

ALTER TABLE tasks ADD CONSTRAINT fk_agent_id_agents_id FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE;

ALTER TABLE learning_plans ADD CONSTRAINT fk_source_conversation_id_conversations_id FOREIGN KEY(source_conversation_id) REFERENCES conversations (id) ON DELETE SET NULL;

ALTER TABLE learning_plans ADD CONSTRAINT fk_source_message_id_messages_id FOREIGN KEY(source_message_id) REFERENCES messages (id) ON DELETE SET NULL;

ALTER TABLE messages ADD CONSTRAINT fk_answer_exercise_id_exercises_id FOREIGN KEY(answer_exercise_id) REFERENCES exercises (id) ON DELETE SET NULL;

ALTER TABLE messages ADD CONSTRAINT fk_user_message_id_messages_id FOREIGN KEY(user_message_id) REFERENCES messages (id) ON DELETE CASCADE;

ALTER TABLE tasks ADD CONSTRAINT fk_file_id_files_id FOREIGN KEY(file_id) REFERENCES files (id) ON DELETE SET NULL;

ALTER TABLE tasks ADD CONSTRAINT fk_conversation_id_conversations_id FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE;

ALTER TABLE sources ADD CONSTRAINT fk_chunk_id_document_chunks_id FOREIGN KEY(chunk_id) REFERENCES document_chunks (id) ON DELETE SET NULL;

ALTER TABLE messages ADD CONSTRAINT fk_task_id_tasks_id FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE SET NULL;

ALTER TABLE tasks ADD CONSTRAINT fk_target_plan_id_learning_plans_id FOREIGN KEY(target_plan_id) REFERENCES learning_plans (id) ON DELETE SET NULL;

ALTER TABLE learning_plans ADD CONSTRAINT fk_last_updated_by_task_id_tasks_id FOREIGN KEY(last_updated_by_task_id) REFERENCES tasks (id) ON DELETE SET NULL;
