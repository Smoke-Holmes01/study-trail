"""Database-level ownership and immutable-history guards for the MySQL baseline."""


def statements():
    guards = {
        "sources": [
            ("file_id", "files", "r.id = NEW.file_id AND r.owner_id = NEW.owner_id", []),
            ("chunk_id", "document_chunks", "r.id = NEW.chunk_id AND r.owner_id = NEW.owner_id AND (NEW.file_id IS NULL OR r.file_id = NEW.file_id)", ["file_id"]),
        ],
        "learning_plans": [
            ("source_conversation_id", "conversations", "r.id = NEW.source_conversation_id AND r.owner_id = NEW.owner_id AND r.agent_id = NEW.agent_id", ["agent_id"]),
            ("source_message_id", "messages", "r.id = NEW.source_message_id AND r.owner_id = NEW.owner_id AND r.agent_id = NEW.agent_id", ["agent_id"]),
            ("last_updated_by_task_id", "tasks", "r.id = NEW.last_updated_by_task_id AND r.owner_id = NEW.owner_id", []),
        ],
        "messages": [
            ("user_message_id", "messages", "r.id = NEW.user_message_id AND r.owner_id = NEW.owner_id AND r.conversation_id = NEW.conversation_id AND r.role = 'user'", ["agent_id", "conversation_id"]),
            ("answer_exercise_id", "exercises", "r.id = NEW.answer_exercise_id AND r.owner_id = NEW.owner_id AND r.agent_id = NEW.agent_id", ["agent_id", "conversation_id"]),
            ("exercise_revision_id", "exercise_revisions", "r.id = NEW.exercise_revision_id AND r.owner_id = NEW.owner_id", []),
        ],
        "tasks": [
            ("agent_id", "agents", "r.id = NEW.agent_id AND r.owner_id = NEW.owner_id", []),
            ("conversation_id", "conversations", "r.id = NEW.conversation_id AND r.owner_id = NEW.owner_id AND r.agent_id = NEW.agent_id", ["agent_id"]),
            ("user_message_id", "messages", "r.id = NEW.user_message_id AND r.owner_id = NEW.owner_id AND r.conversation_id = NEW.conversation_id AND r.role = 'user'", ["agent_id", "conversation_id"]),
            ("assistant_message_id", "messages", "r.id = NEW.assistant_message_id AND r.owner_id = NEW.owner_id AND r.conversation_id = NEW.conversation_id AND r.role = 'assistant'", ["agent_id", "conversation_id"]),
            ("file_id", "files", "r.id = NEW.file_id AND r.owner_id = NEW.owner_id", []),
            ("target_plan_id", "learning_plans", "r.id = NEW.target_plan_id AND r.owner_id = NEW.owner_id AND r.agent_id = NEW.agent_id", ["agent_id"]),
            ("exercise_revision_id", "exercise_revisions", "r.id = NEW.exercise_revision_id AND r.owner_id = NEW.owner_id", []),
        ],
        "exercises": [
            ("message_id", "messages", "r.id=NEW.message_id AND r.owner_id=NEW.owner_id AND r.agent_id=NEW.agent_id", ["agent_id"]),
            ("conversation_id", "conversations", "r.id=NEW.conversation_id AND r.owner_id=NEW.owner_id AND r.agent_id=NEW.agent_id", ["agent_id"]),
            ("current_revision_id", "exercise_revisions", "r.id=NEW.current_revision_id AND r.exercise_id=NEW.id AND r.owner_id=NEW.owner_id", []),
        ],
        "exercise_plan_links": [
            ("plan_id", "learning_plans", "r.id=NEW.plan_id AND r.owner_id=NEW.owner_id AND EXISTS(SELECT 1 FROM exercises e WHERE e.id=NEW.exercise_id AND e.agent_id=r.agent_id AND e.owner_id=r.owner_id)", ["exercise_id"]),
        ],
        "template_import_batches": [
            ("target_kb_id", "knowledge_bases", "r.id=NEW.target_kb_id AND r.owner_id=NEW.owner_id", []),
        ],
        "template_source_mappings": [
            ("file_id", "files", "r.id=NEW.file_id AND r.owner_id=NEW.owner_id", []),
            ("source_id", "sources", "r.id=NEW.source_id AND r.owner_id=NEW.owner_id", []),
        ],
    }
    result = []
    for table, refs in guards.items():
        for event in ("INSERT", "UPDATE"):
            body = []
            for field, target, match, extra in refs:
                changed = "TRUE" if event == "INSERT" else "(" + " OR ".join(
                    f"NOT (NEW.{key} <=> OLD.{key})" for key in [field, "owner_id", *extra]
                ) + ")"
                body.append(f"IF NEW.{field} IS NOT NULL AND {changed} AND NOT EXISTS (SELECT 1 FROM {target} r WHERE {match}) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='private reference mismatch'; END IF;")
            result.append(f"CREATE TRIGGER guard_{table}_{event.lower()} BEFORE {event} ON {table} FOR EACH ROW BEGIN {' '.join(body)} END")
    for table in ("config_revisions", "credential_versions", "admin_operation_logs", "resource_events", "exercise_revisions", "learning_plan_revisions"):
        for event in ("UPDATE", "DELETE"):
            result.append(f"CREATE TRIGGER immutable_{table}_{event.lower()} BEFORE {event} ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='platform history is immutable'")
    result.append("CREATE TRIGGER guard_current_revision BEFORE UPDATE ON platform_configs FOR EACH ROW BEGIN IF OLD.current_revision_id IS NOT NULL AND NEW.current_revision_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='current revision cannot be cleared'; END IF; END")
    result.append("CREATE TRIGGER guard_generation_transition BEFORE UPDATE ON vector_generations FOR EACH ROW BEGIN IF OLD.state='retired' AND NEW.state<>'retired' OR OLD.state='active' AND NEW.state='staging' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='retired generation cannot reactivate'; END IF; END")
    for event in ("UPDATE", "DELETE"):
        result.append(f"CREATE TRIGGER immutable_template_versions_{event.lower()} BEFORE {event} ON template_versions FOR EACH ROW BEGIN IF OLD.status='published' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='published template is immutable'; END IF; END")
        result.append(f"CREATE TRIGGER immutable_template_assets_{event.lower()} BEFORE {event} ON template_assets FOR EACH ROW BEGIN IF EXISTS(SELECT 1 FROM template_version_assets va JOIN template_versions v ON v.id=va.version_id WHERE va.asset_id=OLD.id AND v.status='published') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='published asset is immutable'; END IF; END")
    for event, ref in (("INSERT", "NEW"), ("UPDATE", "OLD"), ("DELETE", "OLD")):
        result.append(f"CREATE TRIGGER immutable_template_manifest_{event.lower()} BEFORE {event} ON template_version_assets FOR EACH ROW BEGIN IF EXISTS(SELECT 1 FROM template_versions v WHERE v.id={ref}.version_id AND v.status='published') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='published manifest is immutable'; END IF; END")
    return result
