
-- 001_init.sql
-- PersonaChat initial database schema

CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Users
CREATE TABLE users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    nickname VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Characters
CREATE TABLE characters (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    image_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_characters_user_id ON characters(user_id);

-- 3. Character persona analysis
CREATE TABLE character_personas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_id BIGINT NOT NULL UNIQUE
        REFERENCES characters(id) ON DELETE CASCADE,
    basic_info TEXT,
    personality TEXT,
    values_and_beliefs TEXT,
    goals TEXT,
    background TEXT,
    worldview TEXT,
    speech_style TEXT,
    behavior_patterns TEXT,
    summary TEXT,
    is_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    model_name VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Source URLs (maximum 3 per character enforced by the application)
CREATE TABLE character_urls (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_id BIGINT NOT NULL
        REFERENCES characters(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    display_order SMALLINT NOT NULL CHECK (display_order BETWEEN 1 AND 3),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, url),
    UNIQUE (character_id, display_order)
);

-- 5. Retrieved source documents
CREATE TABLE character_source_documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_url_id BIGINT NOT NULL
        REFERENCES character_urls(id) ON DELETE CASCADE,
    title TEXT,
    raw_text TEXT NOT NULL,
    fetched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Source text chunks and their embeddings
CREATE TABLE character_chunks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL
        REFERENCES character_source_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content TEXT NOT NULL,
    embedding VECTOR(768),
    token_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX idx_character_chunks_document_id
    ON character_chunks(document_id);

-- 7. Character relationships extracted from source material
CREATE TABLE character_relationships (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_id BIGINT NOT NULL
        REFERENCES characters(id) ON DELETE CASCADE,
    related_character_name VARCHAR(100) NOT NULL,
    relationship_type VARCHAR(100),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_character_relationships_character_id
    ON character_relationships(character_id);

-- 8. Chat rooms
CREATE TABLE chat_rooms (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    character_id BIGINT NOT NULL
        REFERENCES characters(id) ON DELETE CASCADE,
    title VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_rooms_user_id ON chat_rooms(user_id);

-- 9. Chat messages
CREATE TABLE chat_messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_room_id BIGINT NOT NULL
        REFERENCES chat_rooms(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL
        CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_messages_room_created
    ON chat_messages(chat_room_id, created_at);

-- 10. Long-term conversation memories
CREATE TABLE character_memories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    character_id BIGINT NOT NULL
        REFERENCES characters(id) ON DELETE CASCADE,
    memory_text TEXT NOT NULL,
    embedding VECTOR(768),
    memory_type VARCHAR(50),
    importance SMALLINT NOT NULL DEFAULT 3
        CHECK (importance BETWEEN 1 AND 5),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_character_memories_user_character
    ON character_memories(user_id, character_id);
