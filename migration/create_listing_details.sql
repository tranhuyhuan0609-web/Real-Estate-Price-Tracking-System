CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE listing_details (
    listing_key TEXT PRIMARY KEY REFERENCES listings(listing_key) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    
    project_id TEXT,
    project_name VARCHAR(100),
    
    city_code TEXT,
    city_name VARCHAR(20),
    district_code TEXT,
    district_name VARCHAR(30),
    ward_code TEXT,
    ward_name VARCHAR(30),

    lng DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326),
    
    road_width_m NUMERIC(5, 2),
    legal_status VARCHAR(50),
    
    thumbnail_url TEXT,
    media JSONB NOT NULL DEFAULT '[]'::jsonb,
    
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_valid_coordinates CHECK (
        (lat IS NULL AND lng IS NULL) OR 
        (lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180)
    ),
    CONSTRAINT chk_positive_road_width CHECK (road_width_m IS NULL OR road_width_m > 0),
    CONSTRAINT chk_media_is_array CHECK (jsonb_typeof(media) = 'array')
);

CREATE INDEX idx_details_admin_area ON listing_details(city_code, district_code, ward_code);
CREATE INDEX idx_details_geom ON listing_details USING GIST (geom) WHERE geom IS NOT NULL;