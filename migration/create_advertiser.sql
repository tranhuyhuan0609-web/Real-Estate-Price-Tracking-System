CREATE TABLE advertisers (
    advertiser_id VARCHAR(30) PRIMARY KEY,
    phone VARCHAR(15) UNIQUE,
    name VARCHAR(30),
    email VARCHAR(40),
    kind VARCHAR(15) NOT NULL DEFAULT 'unknown',
    is_verified BOOLEAN NOT NULL DEFAULT false,
    
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_advertiser_kind CHECK (kind IN ('broker', 'owner', 'unknown'))
);
