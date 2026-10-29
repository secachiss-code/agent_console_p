CREATE TABLE threads (
  id TEXT PRIMARY KEY,
  rollout_path TEXT,
  cwd TEXT,
  title TEXT,
  tokens_used INTEGER,
  git_branch TEXT,
  name TEXT
);
