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
            ("answer_exercise_id", "exercises", "r.id = NEW.answer_exercise_id AND r.owner_id = NEW.owner_id AND r.conversation_id = NEW.conversation_id", ["agent_id", "conversation_id"]),
        ],
        "tasks": [
            ("agent_id", "agents", "r.id = NEW.agent_id AND r.owner_id = NEW.owner_id", []),
            ("conversation_id", "conversations", "r.id = NEW.conversation_id AND r.owner_id = NEW.owner_id AND r.agent_id = NEW.agent_id", ["agent_id"]),
            ("user_message_id", "messages", "r.id = NEW.user_message_id AND r.owner_id = NEW.owner_id AND r.conversation_id = NEW.conversation_id AND r.role = 'user'", ["agent_id", "conversation_id"]),
            ("assistant_message_id", "messages", "r.id = NEW.assistant_message_id AND r.owner_id = NEW.owner_id AND r.conversation_id = NEW.conversation_id AND r.role = 'assistant'", ["agent_id", "conversation_id"]),
            ("file_id", "files", "r.id = NEW.file_id AND r.owner_id = NEW.owner_id", []),
            ("target_plan_id", "learning_plans", "r.id = NEW.target_plan_id AND r.owner_id = NEW.owner_id AND r.agent_id = NEW.agent_id", ["agent_id"]),
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
    for table in ("config_revisions", "credential_versions", "admin_operation_logs", "resource_events"):
        for event in ("UPDATE", "DELETE"):
            result.append(f"CREATE TRIGGER immutable_{table}_{event.lower()} BEFORE {event} ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='platform history is immutable'")
    result.append("CREATE TRIGGER guard_current_revision BEFORE UPDATE ON platform_configs FOR EACH ROW BEGIN IF OLD.current_revision_id IS NOT NULL AND NEW.current_revision_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='current revision cannot be cleared'; END IF; END")
    result.append("CREATE TRIGGER guard_generation_transition BEFORE UPDATE ON vector_generations FOR EACH ROW BEGIN IF OLD.state='retired' AND NEW.state<>'retired' OR OLD.state='active' AND NEW.state='staging' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='retired generation cannot reactivate'; END IF; END")
    return result
