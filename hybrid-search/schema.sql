-- Hybrid Memory Search Schema
-- SQLite FTS5 (lexical) + sqlite-vec (semantic) with RRF fusion
-- v2.2.0: adds fact lifecycle columns (status, confidence, valid_from,
--         superseded_by, source_context) for conflict resolution.

-- Main memories table
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',      -- daily-note, skill, reference, config, archive, etc.
    layer TEXT DEFAULT 'episodic',          -- episodic, semantic, procedural
    source TEXT NOT NULL,                  -- filename or path
    score REAL DEFAULT 0.0,                -- importance score (0-1)
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    superseded_by INTEGER DEFAULT NULL,
    -- ─── v2.2.0 fact lifecycle (see CHANGELOG) ───
    subject TEXT DEFAULT NULL,             -- entity the fact is about, e.g. "serveur_prod"
    status TEXT DEFAULT 'active',          -- active | superseded | disputed
    confidence REAL DEFAULT 1.0,           -- 0.0..1.0 certainty of the assertion
    valid_from TEXT DEFAULT NULL,          -- ISO timestamp, when the fact became true
    source_context TEXT DEFAULT NULL,      -- originating message/prompt (audit trail)
    last_confirmed TEXT DEFAULT NULL       -- ISO timestamp of last redundant confirmation
);

-- FTS5 virtual table (external content = memories)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- sqlite-vec virtual table (768-dim float embeddings)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0(
    embedding float[768]
);

-- Triggers to keep FTS5 in sync with memories table
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

-- Trigger: delete from vec on memory deletion
CREATE TRIGGER IF NOT EXISTS memories_vec_ad AFTER DELETE ON memories BEGIN
    DELETE FROM memories_vec WHERE rowid = old.id;
END;

-- Indexes for lifecycle queries (status filtering during retrieval)
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject);
