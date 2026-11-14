PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS characters (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scenes (id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shots (id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(id), position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dialogues (id TEXT PRIMARY KEY, shot_id TEXT NOT NULL REFERENCES shots(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voice_profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generation_jobs (
 id TEXT PRIMARY KEY, episode_id TEXT, owner_id TEXT NOT NULL, stage TEXT NOT NULL,
 input_hash TEXT NOT NULL, input_params TEXT NOT NULL, workflow_version TEXT NOT NULL,
 model_version TEXT NOT NULL, status TEXT NOT NULL, prompt_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 started_at REAL, finished_at REAL, elapsed REAL NOT NULL DEFAULT 0, error TEXT, report TEXT,
 UNIQUE(stage,owner_id,input_hash)
);
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL,
 job_id TEXT REFERENCES generation_jobs(id), path TEXT NOT NULL, sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','generating','generated','review_required','approved','rejected','failed')),
 selected INTEGER NOT NULL DEFAULT 0, metadata TEXT NOT NULL,
 created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS selected_asset ON assets(owner_id,kind) WHERE selected=1;
CREATE TABLE IF NOT EXISTS quality_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL REFERENCES assets(id),
 reviewer TEXT NOT NULL, decision TEXT NOT NULL, notes TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS render_jobs (
 id TEXT PRIMARY KEY REFERENCES generation_jobs(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
 output_path TEXT, status TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_queue ON generation_jobs(stage,status);
PRAGMA user_version = 1;
