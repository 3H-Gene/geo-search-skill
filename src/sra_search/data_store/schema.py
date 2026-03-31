"""SQLite 表结构定义

定义数据库 schema，包含 5 张核心表：
- datasets: 数据集主表
- topics: 主题表
- topic_datasets: 主题-数据集关联表（含审核字段）
- search_history: 搜索历史
- review_log: 审核日志
"""

# datasets 表建表 SQL
CREATE_DATASETS_TABLE = """
CREATE TABLE IF NOT EXISTS datasets (
    gse_id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    pubmed_ids TEXT DEFAULT '[]',
    sra_ids TEXT DEFAULT '[]',
    bioproject_ids TEXT DEFAULT '[]',
    organism TEXT DEFAULT '',
    disease TEXT DEFAULT '',
    organ TEXT DEFAULT '',
    omics_type TEXT DEFAULT '',
    omics_granularity TEXT DEFAULT 'unknown',
    sample_count INTEGER DEFAULT 0,
    platform TEXT DEFAULT '',
    publication_date TEXT DEFAULT '',
    journal TEXT DEFAULT '',
    abstract TEXT DEFAULT '',
    keywords TEXT DEFAULT '[]',
    first_seen_at TEXT DEFAULT '',
    last_updated TEXT DEFAULT '',
    version INTEGER DEFAULT 1,
    change_log TEXT DEFAULT '[]',
    availability_status TEXT DEFAULT 'unverified',
    availability_note TEXT DEFAULT '',
    availability_checked_at TEXT DEFAULT '',
    access_type TEXT DEFAULT 'unknown',
    has_gse INTEGER DEFAULT 1,
    metadata_hash TEXT DEFAULT ''
);
"""

# topics 表建表 SQL
CREATE_TOPICS_TABLE = """
CREATE TABLE IF NOT EXISTS topics (
    topic_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    keywords_used TEXT DEFAULT '[]',
    created_at TEXT DEFAULT '',
    last_searched_at TEXT DEFAULT ''
);
"""

# topic_datasets 关联表建表 SQL
CREATE_TOPIC_DATASETS_TABLE = """
CREATE TABLE IF NOT EXISTS topic_datasets (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    gse_id TEXT NOT NULL,
    match_keyword TEXT DEFAULT '',
    match_source TEXT DEFAULT '',
    match_score REAL DEFAULT 0.0,
    review_status TEXT DEFAULT 'pending',
    review_note TEXT DEFAULT '',
    reviewed_at TEXT DEFAULT '',
    added_at TEXT DEFAULT '',
    FOREIGN KEY (topic_id) REFERENCES topics(topic_id),
    FOREIGN KEY (gse_id) REFERENCES datasets(gse_id)
);
"""

# search_history 表建表 SQL
CREATE_SEARCH_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS search_history (
    id TEXT PRIMARY KEY,
    topic_id TEXT,
    search_time TEXT DEFAULT '',
    keyword_used TEXT DEFAULT '',
    results_count INTEGER DEFAULT 0
);
"""

# review_log 表建表 SQL
CREATE_REVIEW_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS review_log (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    gse_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_status TEXT DEFAULT '',
    new_status TEXT DEFAULT '',
    note TEXT DEFAULT '',
    acted_at TEXT DEFAULT '',
    FOREIGN KEY (topic_id) REFERENCES topics(topic_id)
);
"""

# 索引
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_topic_datasets_topic ON topic_datasets(topic_id);",
    "CREATE INDEX IF NOT EXISTS idx_topic_datasets_gse ON topic_datasets(gse_id);",
    "CREATE INDEX IF NOT EXISTS idx_topic_datasets_review ON topic_datasets(review_status);",
    "CREATE INDEX IF NOT EXISTS idx_datasets_availability ON datasets(availability_status);",
    "CREATE INDEX IF NOT EXISTS idx_datasets_access_type ON datasets(access_type);",
    "CREATE INDEX IF NOT EXISTS idx_datasets_organism ON datasets(organism);",
    "CREATE INDEX IF NOT EXISTS idx_datasets_omics_granularity ON datasets(omics_granularity);",
    "CREATE INDEX IF NOT EXISTS idx_search_history_topic ON search_history(topic_id);",
    "CREATE INDEX IF NOT EXISTS idx_review_log_topic_gse ON review_log(topic_id, gse_id);",
]

# 所有建表语句
ALL_TABLES = [
    CREATE_DATASETS_TABLE,
    CREATE_TOPICS_TABLE,
    CREATE_TOPIC_DATASETS_TABLE,
    CREATE_SEARCH_HISTORY_TABLE,
    CREATE_REVIEW_LOG_TABLE,
]
