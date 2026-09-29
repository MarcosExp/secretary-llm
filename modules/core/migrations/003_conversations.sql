-- Chat history for the web interface, so a conversation survives restarts.
-- `history` is the message list the agent works with (JSON); it is trimmed to the
-- most recent turns when saved.

CREATE TABLE core_conversations (
  id INTEGER PRIMARY KEY,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  history TEXT NOT NULL DEFAULT '[]'
);

CREATE TRIGGER core_conversations_no_delete BEFORE DELETE ON core_conversations
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;
