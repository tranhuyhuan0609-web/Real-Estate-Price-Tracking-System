CREATE TABLE crawler_configs (
    site_id TEXT PRIMARY KEY,
    name VARCHAR(20) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    crawler_type TEXT NOT NULL DEFAULT 'rest_api',
    
    
    schedule_times TEXT[] NOT NULL DEFAULT '{}', 
    interval_seconds INT NOT NULL DEFAULT 300,   
    

    config_json JSONB NOT NULL,
    
    last_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
   
    CONSTRAINT chk_crawler_type CHECK (crawler_type IN ('rest_api', 'graphql', 'html')),
    CONSTRAINT chk_interval_positive CHECK (interval_seconds > 0),
    CONSTRAINT chk_config_is_object CHECK (jsonb_typeof(config_json) = 'object')
);