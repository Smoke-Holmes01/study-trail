CREATE FUNCTION study_trail_private_reference_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_owner uuid;
    target_agent uuid;
    target_conversation uuid;
    target_parent uuid;
BEGIN
    IF TG_TABLE_NAME = 'sources' THEN
        IF NEW.file_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.file_id IS DISTINCT FROM OLD.file_id) THEN
            SELECT owner_id INTO target_owner FROM files WHERE id=NEW.file_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.chunk_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.chunk_id IS DISTINCT FROM OLD.chunk_id) THEN
            SELECT owner_id,file_id INTO target_owner,target_parent FROM document_chunks WHERE id=NEW.chunk_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id OR (NEW.file_id IS NOT NULL AND target_parent IS DISTINCT FROM NEW.file_id)
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'learning_plans' THEN
        IF NEW.source_conversation_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.source_conversation_id IS DISTINCT FROM OLD.source_conversation_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id) THEN
            SELECT owner_id,agent_id INTO target_owner,target_agent FROM conversations WHERE id=NEW.source_conversation_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id OR target_agent IS DISTINCT FROM NEW.agent_id
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.source_message_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.source_message_id IS DISTINCT FROM OLD.source_message_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id) THEN
            SELECT owner_id,agent_id INTO target_owner,target_agent FROM messages WHERE id=NEW.source_message_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id OR target_agent IS DISTINCT FROM NEW.agent_id
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.last_updated_by_task_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.last_updated_by_task_id IS DISTINCT FROM OLD.last_updated_by_task_id) THEN
            SELECT owner_id INTO target_owner FROM tasks WHERE id=NEW.last_updated_by_task_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'messages' THEN
        IF NEW.user_message_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.user_message_id IS DISTINCT FROM OLD.user_message_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id) THEN
            SELECT owner_id,conversation_id INTO target_owner,target_conversation FROM messages WHERE id=NEW.user_message_id AND role='user';
            IF target_owner IS DISTINCT FROM NEW.owner_id OR target_conversation IS DISTINCT FROM NEW.conversation_id
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.answer_exercise_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.answer_exercise_id IS DISTINCT FROM OLD.answer_exercise_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id) THEN
            SELECT owner_id,conversation_id INTO target_owner,target_conversation FROM exercises WHERE id=NEW.answer_exercise_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id OR target_conversation IS DISTINCT FROM NEW.conversation_id
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'tasks' THEN
        IF NEW.agent_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id) THEN
            SELECT owner_id INTO target_owner FROM agents WHERE id=NEW.agent_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.conversation_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id) THEN
            SELECT owner_id,agent_id INTO target_owner,target_agent FROM conversations WHERE id=NEW.conversation_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id OR target_agent IS DISTINCT FROM NEW.agent_id
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.user_message_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.user_message_id IS DISTINCT FROM OLD.user_message_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id) THEN
            SELECT owner_id,conversation_id INTO target_owner,target_conversation FROM messages WHERE id=NEW.user_message_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id OR target_conversation IS DISTINCT FROM NEW.conversation_id
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.assistant_message_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.assistant_message_id IS DISTINCT FROM OLD.assistant_message_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id) THEN
            SELECT owner_id,conversation_id INTO target_owner,target_conversation FROM messages WHERE id=NEW.assistant_message_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id OR target_conversation IS DISTINCT FROM NEW.conversation_id
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.file_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.file_id IS DISTINCT FROM OLD.file_id) THEN
            SELECT owner_id INTO target_owner FROM files WHERE id=NEW.file_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
        IF NEW.target_plan_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.target_plan_id IS DISTINCT FROM OLD.target_plan_id OR NEW.agent_id IS DISTINCT FROM OLD.agent_id) THEN
            SELECT owner_id,agent_id INTO target_owner,target_agent FROM learning_plans WHERE id=NEW.target_plan_id;
            IF target_owner IS DISTINCT FROM NEW.owner_id OR target_agent IS DISTINCT FROM NEW.agent_id
            THEN RAISE EXCEPTION 'private reference mismatch'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER private_sources BEFORE INSERT OR UPDATE ON sources FOR EACH ROW EXECUTE FUNCTION study_trail_private_reference_guard();
CREATE TRIGGER private_plans BEFORE INSERT OR UPDATE ON learning_plans FOR EACH ROW EXECUTE FUNCTION study_trail_private_reference_guard();
CREATE TRIGGER private_messages BEFORE INSERT OR UPDATE ON messages FOR EACH ROW EXECUTE FUNCTION study_trail_private_reference_guard();
CREATE TRIGGER private_tasks BEFORE INSERT OR UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION study_trail_private_reference_guard();
