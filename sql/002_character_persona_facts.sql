-- 002_character_persona_facts.sql
-- Accumulated persona facts from sequential multi-URL document analysis.
-- Document processing completion is tracked via status = 'document_processed'
-- sentinel rows (even when a document yields zero extractable facts).

CREATE TABLE character_persona_facts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_id BIGINT NOT NULL
        REFERENCES characters(id) ON DELETE CASCADE,
    field_name VARCHAR(100) NOT NULL,
    fact_text TEXT NOT NULL DEFAULT '',
    source_document_ids BIGINT[] NOT NULL DEFAULT '{}',
    status VARCHAR(50) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'conflict', 'document_processed')),
    related_fact_id BIGINT
        REFERENCES character_persona_facts(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_character_persona_facts_character_id
    ON character_persona_facts(character_id);

CREATE INDEX idx_character_persona_facts_character_status
    ON character_persona_facts(character_id, status);

CREATE INDEX idx_character_persona_facts_source_docs
    ON character_persona_facts USING GIN (source_document_ids);
